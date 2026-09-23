"""Synthetic regressions for Illustrator state carried across layer boundaries."""

import base64
import hashlib
import io
from unittest.mock import patch

import fitz
import pytest
from conftest import NEW, frappe
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject
from test_layers import rendered, stream
from test_pdf import pdf_fixture

from igctools.printcard.layers import _Separator


def fixture(path, *, nested=False, rotation=0, pending_path=False, text=False):
	writer = PdfWriter()
	page = writer.add_blank_page(width=288, height=216)
	page[NameObject("/Rotate")] = NumberObject(rotation)
	font = DictionaryObject(
		{
			NameObject("/Type"): NameObject("/Font"),
			NameObject("/Subtype"): NameObject("/Type1"),
			NameObject("/BaseFont"): NameObject("/Helvetica"),
		}
	)
	resources = DictionaryObject(
		{
			NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
			NameObject("/ExtGState"): DictionaryObject(
				{NameObject("/Alpha"): DictionaryObject({NameObject("/ca"): FloatObject(0.5)})}
			),
		}
	)
	# A preceding visible layer leaves a transformed glue-flap clip and transparency
	# active. Its closing Q occurs in the visible layer AFTER the hidden metadata.
	state = b"q 1 0 0 1 8 0 cm 0 0 12 200 re W n /Alpha gs "
	paint = b"1 0 0 rg 0 0 200 200 re f "
	pending = b"0 1 0 rg 0 0 60 20 re " if pending_path else b""
	if text:
		pending += b"BT /F1 9 Tf 0 0 0 rg 1 0 0 1 0 80 Tm (A) Tj "
	tail = (b"(B) Tj ET " if text else b"") + (b"f " if pending_path else b"")
	tail += b"0 1 0 rg 0 0 200 10 re f Q 0 0 0 RG 25 15 60 50 re S "
	# Each independent hidden stream retains its OWN transform, clip and font.
	hidden_paints = [
		b"q 1 0 0 1 25 15 cm 0 0 60 50 re W n 0 0 1 rg -10 -10 100 100 re f Q ",
		b"q 0 0 0 rg BT /F1 9 Tf 1 0 0 1 80 90 Tm (100 mm) Tj ET Q ",
	]
	properties = DictionaryObject()
	resources[NameObject("/Properties")] = properties
	hidden_blocks = []
	for i, (label, data) in enumerate(zip(["BARNIZ BRILLO", "DIMENSIONES"], hidden_paints, strict=True)):
		key = NameObject(f"/H{i}")
		properties[key] = DictionaryObject(
			{
				NameObject("/AIType"): NameObject("/HiddenLayer"),
				NameObject("/Contents"): writer._add_object(stream(data)),
				NameObject("/Resources"): DictionaryObject({NameObject("/Font"): resources["/Font"]}),
			}
		)
		hidden_blocks.append(f"/Layer << /Title ({label}) >> BDC /AltAI8 {key} BDC EMC EMC ".encode())
	layer_start = b"/Layer << /Title (TROQUEL) >> BDC "
	contents = layer_start + state + paint + pending + b"EMC "
	contents += b"".join(hidden_blocks) + layer_start + tail + b"EMC "
	# Independently specified expected painting order: visible artwork, hidden
	# streams from entry state, then the rest of the original visible scope.
	expected = state + paint + b"Q " + b"".join(hidden_paints) + state + pending + tail

	def set_contents(data):
		if nested:
			form = stream(data)
			form.update(
				{
					NameObject("/Subtype"): NameObject("/Form"),
					NameObject("/BBox"): page.mediabox,
					NameObject("/Matrix"): ArrayObject([FloatObject(v) for v in [0.8, 0, 0, 0.8, 12, 6]]),
					NameObject("/Resources"): resources,
				}
			)
			page[NameObject("/Resources")] = DictionaryObject(
				{NameObject("/XObject"): DictionaryObject({NameObject("/Outer"): writer._add_object(form)})}
			)
			data = b"q 1 0 0 1 15 10 cm 0 0 180 170 re W n /Outer Do Q"
		else:
			page[NameObject("/Resources")] = resources
		page[NameObject("/Contents")] = writer._add_object(stream(data))

	set_contents(contents)
	path.write_bytes(_bytes(writer))
	set_contents(expected)
	buffer = io.BytesIO()
	writer.write(buffer)
	return path, PdfReader(buffer)


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("rotation", [0, 90])
@pytest.mark.parametrize("pending_path,text", [(False, False), (True, False), (False, True)])
def test_hidden_layers_do_not_inherit_previous_layer_state(tmp_path, nested, rotation, pending_path, text):
	source, expected = fixture(
		tmp_path / "source.pdf", nested=nested, rotation=rotation, pending_path=pending_path, text=text
	)
	separator = _Separator(PdfReader(source))
	writer = PdfWriter()
	writer.add_page(separator.variant({"TROQUEL", "BARNIZ BRILLO", "DIMENSIONES"}))
	with rendered(PdfReader(io.BytesIO(_bytes(writer)))) as actual, rendered(expected) as reference:
		assert actual[0].get_pixmap().samples == reference[0].get_pixmap().samples
		assert "100 mm" in actual[0].get_text()


def _bytes(writer):
	buffer = io.BytesIO()
	writer.write(buffer)
	return buffer.getvalue()


@pytest.mark.parametrize("orientation", ["Landscape", "Portrait"])
def test_preview_generated_and_signed_keep_complete_hidden_artwork(tmp_path, orientation):
	source, pc, _cv = pdf_fixture(tmp_path, orientation)
	fixture(source)
	digest = hashlib.sha256(source.read_bytes()).digest()
	helper = NEW["helper.py"]
	helper.generate_pdf_for_printcard(printcard=pc.name)
	preview = frappe.local.response.filecontent
	pc.save.assert_not_called()
	with patch.object(helper, "get_unique_filename", return_value="generated.pdf"):
		pc.printcard_file = helper.generate_pdf_for_printcard(printcard=pc.name, pdf_path=True)
	with fitz.open(stream=preview, filetype="pdf") as a, fitz.open(source.parent / "generated.pdf") as b:
		assert len(a) == len(b) == 3
		assert "100 mm" in a[1].get_text()
		for pa, pb in zip(a, b, strict=True):
			assert pa.get_pixmap().samples == pb.get_pixmap().samples
			assert "PRINTCARD ACME" in pa.get_text()
		assert any(d["fill"] == (0, 0, 1) for d in a[2].get_drawings())
		# Extracted vectors/text can exist even when the renderer clips them away.
		pixels = a[2].get_pixmap().samples
		assert sum(r < 30 and g < 30 and b > 240 for r, g, b in zip(*[iter(pixels)] * 3, strict=True)) > 1000
	image = Image.new("RGBA", (20, 10), (0, 0, 0, 255))
	buffer = io.BytesIO()
	image.save(buffer, format="PNG")
	pc.firma_cliente = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
	frappe.session.user = "qa@example.test"
	with patch.object(helper, "get_unique_filename", return_value="signed.pdf"):
		helper._sign_pdf_with_base64(pc.name)
	with fitz.open(source.parent / "generated.pdf") as a, fitz.open(source.parent / "signed.pdf") as b:
		assert len(a) == len(b) == 3
		assert "100 mm" in b[1].get_text()
		for pa, pb in zip(a, b, strict=True):
			assert len(pb.get_images()) == 1
			assert pa.get_drawings() == pb.get_drawings()
	assert hashlib.sha256(source.read_bytes()).digest() == digest
