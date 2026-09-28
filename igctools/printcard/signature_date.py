"""Recover the approval date printed beside FECHA, never the PDF modification date."""

import re
from datetime import date

import fitz

_NUMBER = rb"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
_IMAGE_STREAM = re.compile(rb"\s*q\s+(?:" + _NUMBER + rb"\s+){6}cm\s+/(?P<image>fzImg\d+)\s+Do\s+Q\s*")
_DATE_STREAM = re.compile(
	rb"\s*q\s+BT\s+1\s+0\s+0\s+1\s+"
	+ _NUMBER
	+ rb"\s+"
	+ _NUMBER
	+ rb"\s+Tm\s+/helv\s+"
	+ _NUMBER
	+ rb"\s+Tf\s+(?:"
	+ _NUMBER
	+ rb"\s+){3}RG\s+(?:"
	+ _NUMBER
	+ rb"\s+){3}rg\s+\[<(?P<date>[0-9a-fA-F]{20})>\]\s*TJ\s+ET\s+Q\s*"
)


def _appended_signature_date(page):
	"""Recognize this app's exact image-then-date overlay, including misplaced dates.

	Older canvases sometimes put the signature far from FECHA. The signer appended
	two separate content streams: one registered image and one Helvetica ISO date.
	Do not treat an arbitrary last date in the customer's artwork as a signature.
	"""
	contents = page.get_contents()
	if len(contents) < 3:
		return None
	image = _IMAGE_STREAM.fullmatch(page.parent.xref_stream(contents[-2]))
	date_text = _DATE_STREAM.fullmatch(page.parent.xref_stream(contents[-1]))
	if not image or not date_text:
		return None
	image_name = image["image"].decode("ascii")
	if not any(item[7] == image_name for item in page.get_images(full=True)):
		return None
	if not any(font[3] == "Helvetica" and font[4] == "helv" for font in page.get_fonts()):
		return None
	return validate_signature_date(bytes.fromhex(date_text["date"].decode("ascii")).decode("ascii"))


def validate_signature_date(value: str) -> str:
	if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
		raise ValueError("La fecha de firma debe tener el formato AAAA-MM-DD.")
	return date.fromisoformat(value).isoformat()


def original_signature_date(pdf_path: str) -> str:
	"""Require one unambiguous signature date on every signed page.

	Use the exact overlay produced by our signer, or the FECHA/FIRMA footer for
	PDFs whose streams have been consolidated. Both sources must agree if present.
	Missing files, unreadable dates and conflicting pages intentionally fail closed.
	"""
	dates = set()
	with fitz.open(pdf_path) as pdf:
		if not len(pdf) or pdf.needs_pass:
			raise ValueError("No se puede leer el PDF firmado original.")
		for number, page in enumerate(pdf, start=1):
			appended_date = _appended_signature_date(page)
			words = page.get_text("words")
			labels = [fitz.Rect(w[:4]) for w in words if w[4].strip().upper() == "FECHA"]
			signatures = [fitz.Rect(w[:4]) for w in words if w[4].strip().upper() == "FIRMA"]
			candidates = set()
			for label in labels:
				if not any(abs(label.y0 - sign.y0) <= 12 and sign.x0 > label.x1 for sign in signatures):
					continue
				for word in words:
					if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", word[4]):
						continue
					box = fitz.Rect(word[:4])
					near_x = abs((box.x0 + box.x1 - label.x0 - label.x1) / 2) <= 108
					near_y = label.y0 - 144 <= box.y0 <= label.y1 + 24
					if near_x and near_y:
						candidates.add(validate_signature_date(word[4]))
			if appended_date:
				candidates.add(appended_date)
			if len(candidates) != 1:
				raise ValueError(f"No se pudo verificar una fecha de firma única en la página {number}.")
			dates.update(candidates)
	if len(dates) != 1:
		raise ValueError("El PDF firmado original contiene fechas de firma diferentes entre páginas.")
	return dates.pop()
