"""Recover the approval date printed beside FECHA, never the PDF modification date."""

import re
from datetime import date

import fitz


def validate_signature_date(value: str) -> str:
	if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
		raise ValueError("La fecha de firma debe tener el formato AAAA-MM-DD.")
	return date.fromisoformat(value).isoformat()


def original_signature_date(pdf_path: str) -> str:
	"""Require one unambiguous date in the signature footer on every signed page.

	The old and new canvases place FECHA and FIRMA on the same line. Looking
	beside those labels excludes dates in the customer's artwork and PDF metadata.
	Missing files, unreadable dates and conflicting pages intentionally fail closed.
	"""
	dates = set()
	with fitz.open(pdf_path) as pdf:
		if not len(pdf) or pdf.needs_pass:
			raise ValueError("No se puede leer el PDF firmado original.")
		for number, page in enumerate(pdf, start=1):
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
			if len(candidates) != 1:
				raise ValueError(f"No se pudo verificar una fecha de firma única en la página {number}.")
			dates.update(candidates)
	if len(dates) != 1:
		raise ValueError("El PDF firmado original contiene fechas de firma diferentes entre páginas.")
	return dates.pop()
