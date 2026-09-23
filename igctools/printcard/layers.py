"""Vector page separation for single-page PrintCard artwork.

Recognizes PDF OCGs and Illustrator /Layer marked content, including the
/AltAI8 /HiddenLayer streams used by Illustrator for hidden artwork. The
original is read only; ordinary and multi-page PDFs retain their old behavior.
"""

import re
from io import BytesIO

import fitz
from pypdf import PageObject, PdfReader, PdfWriter
from pypdf.generic import (
	ContentStream,
	DecodedStreamObject,
	DictionaryObject,
	NameObject,
	NumberObject,
)

PAGE_GROUPS = (
	("ARTE + TROQUEL + PRESERVADO", frozenset({"ARTE", "TROQUEL", "PRESERVADO"})),
	("TROQUEL", frozenset({"TROQUEL"})),
	("RELIEVE", frozenset({"RELIEVE"})),
	("ESTAMPADO", frozenset({"ESTAMPADO"})),
	("BARNIZ BRILLO", frozenset({"BARNIZ BRILLO"})),
	("BARNIZ MATTE", frozenset({"BARNIZ MATTE"})),
)
# Reference artwork never determines whether an optional page exists.
PAGE_REFERENCES = {
	"TROQUEL": frozenset({"DIMENSIONES"}),
	"RELIEVE": frozenset({"TROQUEL"}),
	"BARNIZ BRILLO": frozenset({"TROQUEL"}),
	"BARNIZ MATTE": frozenset({"TROQUEL"}),
}
KNOWN_LAYERS = frozenset().union(*(names for _, names in PAGE_GROUPS), {"DIMENSIONES"})
PATH_PAINT = {b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"}
TEXT_PAINT = {b"Tj", b"TJ", b"'", b'"'}
PATH_BUILD = {b"m", b"l", b"c", b"v", b"y", b"h", b"re"}


class LayerSeparationError(ValueError):
	"""A layered source cannot safely be separated; do not save a partial result."""


def _name(value):
	# Accept the optional numbering shown in Illustrator ("1. TROQUEL").
	return re.sub(r"^\d+\s*[.)-]\s*", "", " ".join(str(value or "").upper().split()))


def _object(value):
	return value.get_object() if hasattr(value, "get_object") else value


def _properties(value, resources):
	if isinstance(value, NameObject):
		value = _object(resources.get("/Properties", {})).get(value, {})
	return _object(value)


def _owner(properties, parent):
	if properties.get("/Type") == "/OCMD":
		raise LayerSeparationError("El PDF usa condiciones de visibilidad OCMD no soportadas.")
	name = _name(properties.get("/Title") or properties.get("/Name"))
	# All descendants of a production group belong to that group.
	return parent if parent in KNOWN_LAYERS else name or parent


def _isolate_hidden_calls(operations, hidden_names):
	"""Paint Illustrator's independent hidden streams in the enclosing stream's entry state.

	Marked-content boundaries do not save/restore PDF graphics state. In particular,
	Illustrator may close PRESERVADO's clipping scope only at the start of the next
	visible layer, after several /AltAI8 records. A q/Do/Q at those records inherits
	that unrelated clip. Unwind to our entry checkpoint, paint the hidden Form, then
	replay state without paint to resume the visible stream at precisely that point.
	Keeping this local to each stream retains enclosing Form matrices and clips.
	"""
	if not hidden_names:
		return operations
	out, replay, frames = [([], b"q")], [], []
	path_open, text_open, text_mode = False, False, 0
	for operands, operator in operations:
		if operator == b"Do" and operands[0] in hidden_names:
			if text_open:
				out.append(([], b"ET"))
			# Paths are not part of q/Q state. Rebuild a pending path from replay below.
			out.append(([], b"n"))
			out.extend(([], b"Q") for _ in range(len(frames) + 1))
			out.extend([([], b"q"), (operands, operator), ([], b"Q"), ([], b"q")])
			out.extend(replay)
			continue
		out.append((operands, operator))
		if operator == b"q":
			frames.append((len(replay), text_mode))
		elif operator == b"Q":
			if not frames:
				raise LayerSeparationError("El PDF contiene una restauración gráfica sin apertura.")
			checkpoint, text_mode = frames.pop()
			if not path_open and not text_open:
				# Closed scopes have no effect on future state. Do not replay their artwork.
				del replay[checkpoint:]
				replay.append(([], b"n"))
				continue
		elif operator in PATH_BUILD:
			path_open = True
		elif operator in PATH_PAINT or operator == b"n":
			path_open = False
		elif operator == b"BT":
			text_open = True
		elif operator == b"ET":
			text_open = False
		elif operator == b"Tr":
			text_mode = int(operands[0])
		if operator in PATH_PAINT:
			replay.append(([], b"n"))
		elif operator in TEXT_PAINT:
			# Keep text advances and text clipping, without painting text a second time.
			mode = NumberObject(7 if text_mode >= 4 else 3)
			replay.extend([([], b"q"), ([mode], b"Tr"), (operands, operator), ([], b"Q")])
		elif operator not in (b"Do", b"sh", b"INLINE IMAGE"):
			replay.append((operands, operator))
	# Enclosing Forms/pages implicitly discard residual saves; contain them explicitly.
	out.extend(([], b"Q") for _ in range(len(frames) + 1))
	return out


class _Separator:
	def __init__(self, reader):
		self.reader = reader
		self.page = reader.pages[0]
		self.names = set()
		self._parsed = {}
		self.contents = self.page.get_contents()

	def operations(self, stream):
		stream = _object(stream)
		key = id(stream)
		if key not in self._parsed:
			self._parsed[key] = ContentStream(stream, self.reader).operations
		return self._parsed[key]

	def has_production_layers(self, stream, resources, seen=None, depth=0):
		"""Avoid rewriting or validating unrelated PDFs, including their annotations."""
		seen = set() if seen is None else seen
		stream, resources = _object(stream), _object(resources)
		if id(stream) in seen or depth > 32:
			return False
		seen.add(id(stream))
		for operands, operator in self.operations(stream):
			if operator == b"BDC" and operands[0] in ("/Layer", "/OC"):
				prop = _properties(operands[1], resources)
				if _name(prop.get("/Title") or prop.get("/Name")) in KNOWN_LAYERS - {"DIMENSIONES"}:
					return True
			if operator == b"Do":
				form = _object(_object(resources.get("/XObject", {}))[operands[0]])
				prop = _object(form.get("/OC", {}))
				if _name(prop.get("/Name")) in KNOWN_LAYERS - {"DIMENSIONES"}:
					return True
				if form.get("/Subtype") == "/Form" and self.has_production_layers(
					form, form.get("/Resources", resources), seen, depth + 1
				):
					return True
		return False

	def filter(self, stream, resources, selected, parent=None, depth=0):
		if depth > 32:
			raise LayerSeparationError("El PDF tiene grupos recursivos o demasiados niveles de capas.")
		resources = _object(resources)
		out_resources = DictionaryObject(
			{key: value for key, value in resources.items() if key not in ("/Properties", "/XObject")}
		)
		xobjects = DictionaryObject()
		out_resources[NameObject("/XObject")] = xobjects
		hidden_names = set()
		operations, stack = [], [parent]
		for operands, operator in self.operations(stream):
			owner = stack[-1]
			active = owner in selected
			if operator in (b"BDC", b"BMC"):
				prop = _properties(operands[1], resources) if operator == b"BDC" else {}
				if operands[0] in ("/Layer", "/OC"):
					owner = _owner(prop, owner)
					self.names.add(owner)
				stack.append(owner)
				if prop.get("/AIType") == "/HiddenLayer" and owner in selected:
					if "/Contents" not in prop or "/Resources" not in prop:
						raise LayerSeparationError("Una capa oculta no contiene sus datos o recursos PDF.")
					contents, hidden_resources = self.filter(
						prop["/Contents"], prop["/Resources"], selected, owner, depth + 1
					)
					form = DecodedStreamObject()
					form.set_data(contents.get_data())
					form.update(
						{
							NameObject("/Type"): NameObject("/XObject"),
							NameObject("/Subtype"): NameObject("/Form"),
							NameObject("/BBox"): self.page.mediabox,
							NameObject("/Resources"): hidden_resources,
						}
					)
					key = NameObject(f"/IGCHidden{len(xobjects)}")
					while key in _object(resources.get("/XObject", {})):
						key = NameObject(str(key) + "_")
					xobjects[key] = form.flate_encode()
					hidden_names.add(key)
					operations.extend([([], b"q"), ([key], b"Do"), ([], b"Q")])
				continue
			if operator == b"EMC":
				if len(stack) == 1:
					raise LayerSeparationError("El PDF contiene un cierre de capa sin apertura.")
				stack.pop()
				continue
			if operator in (b"MP", b"DP"):
				continue
			if operator == b"Do":
				xobject = _object(_object(resources.get("/XObject", {}))[operands[0]])
				if "/OC" in xobject:
					owner = _owner(_object(xobject["/OC"]), owner)
					self.names.add(owner)
				active = owner in selected
				if xobject.get("/Subtype") == "/Form":
					contents, form_resources = self.filter(
						xobject, xobject.get("/Resources", resources), selected, owner, depth + 1
					)
					form = DecodedStreamObject()
					form.update(
						{
							key: value
							for key, value in xobject.items()
							if key
							not in ("/Length", "/Filter", "/DecodeParms", "/Resources", "/OC", "/PieceInfo")
						}
					)
					form.set_data(contents.get_data())
					form[NameObject("/Resources")] = form_resources
					# A reused Form can occur under different layer parents.
					key = NameObject(f"/IGCForm{len(xobjects)}")
					while key in _object(resources.get("/XObject", {})):
						key = NameObject(str(key) + "_")
					xobjects[key] = form.flate_encode()
					operations.append(([key], operator))
				elif active:
					# Copy the dictionary before removing optional visibility metadata.
					image = xobject.clone(PdfWriter())
					image.pop(NameObject("/OC"), None)
					xobjects[operands[0]] = image
					operations.append((operands, operator))
				continue
			if not active:
				if operator in PATH_PAINT:
					# End the path without painting, preserving clipping and graphics state.
					operations.append(([], b"n"))
					continue
				if operator in TEXT_PAINT:
					# Invisible text preserves text advances and the following text position.
					operations.extend(
						[([], b"q"), ([NumberObject(3)], b"Tr"), (operands, operator), ([], b"Q")]
					)
					continue
				if operator in (b"sh", b"INLINE IMAGE"):
					continue
			operations.append((operands, operator))
		if len(stack) != 1:
			raise LayerSeparationError("El PDF contiene una capa sin cerrar.")
		output = ContentStream(None, self.reader)
		output.operations = _isolate_hidden_calls(operations, hidden_names)
		return output, out_resources

	def variant(self, selected):
		contents, resources = self.filter(self.contents, self.page["/Resources"], selected)
		page = PageObject()
		page.update(
			{
				key: value
				for key, value in self.page.items()
				if key not in ("/Parent", "/Contents", "/Resources", "/PieceInfo", "/Metadata", "/Annots")
			}
		)
		# Materialize operations before compression (pypdf 3.x and 6.x).
		contents.get_data()
		page[NameObject("/Contents")] = contents.flate_encode()
		page[NameObject("/Resources")] = resources
		return page


def _has_content(page):
	"""Check painted coverage, not drawing commands inside unused soft masks.

	The alpha channel also keeps white artwork, while clipping, zero opacity and
	mask setup without paint stay empty. Probe bounded tiles at 144 dpi without
	rasterizing or changing the vector page that is returned to the customer.
	"""
	writer = PdfWriter()
	writer.add_page(page)
	buffer = BytesIO()
	writer.write(buffer)
	with fitz.open(stream=buffer.getvalue(), filetype="pdf") as pdf:
		visible = pdf[0].rect
		display = pdf[0].get_displaylist(annots=False)
		for y in range(0, int(visible.height) + 1, 512):
			for x in range(0, int(visible.width) + 1, 512):
				clip = fitz.Rect(x, y, x + 512, y + 512) & visible
				if clip.is_empty:
					continue
				pixmap = display.get_pixmap(
					matrix=fitz.Matrix(2, 2), colorspace=fitz.csGRAY, alpha=True, clip=clip
				)
				# Gray + alpha: any painted pixel is enough, regardless of its color.
				if pixmap.samples[1::2].strip(b"\x00"):
					return True
	return False


def prepare_printcard_source(source):
	"""Return original pages or ordered vector separations, without writing source.

	The first composition always exists. Optional pages require painted content
	in their main group before reference layers are added; references alone never
	create a page for an empty or absent production group.
	"""
	reader = PdfReader(source)
	if len(reader.pages) != 1:
		return reader
	separator = _Separator(reader)
	if not separator.has_production_layers(separator.contents, separator.page.get("/Resources", {})):
		return reader
	first = separator.variant(PAGE_GROUPS[0][1])
	if not (separator.names & (KNOWN_LAYERS - {"DIMENSIONES"})):
		return reader
	if reader.pages[0].get("/Annots"):
		raise LayerSeparationError("El PDF por capas contiene anotaciones; exporta el arte sin anotaciones.")
	writer = PdfWriter()
	writer.add_page(first)
	writer.add_outline_item(PAGE_GROUPS[0][0], 0)
	page_labels = [PAGE_GROUPS[0][0]]
	for label, selected in PAGE_GROUPS[1:]:
		if not separator.names & selected:
			continue
		page = separator.variant(selected)
		if _has_content(page):
			page_labels.append(label)
			if references := PAGE_REFERENCES.get(label):
				# Draw reference lines last so solid varnish fills cannot obscure them.
				page.merge_page(separator.variant(references))
				label += " + " + " + ".join(sorted(references))
			writer.add_page(page)
			writer.add_outline_item(label, len(writer.pages) - 1)
	buffer = BytesIO()
	writer.write(buffer)
	buffer.seek(0)
	result = PdfReader(buffer)
	for page, label in zip(result.pages, page_labels, strict=True):
		# In-memory only: an ordinary input PDF cannot opt itself into labelling.
		page.printcard_separation_label = label
	return result
