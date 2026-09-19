"""Verify separation labels without moving, cropping or rasterizing the artwork."""

from unittest.mock import patch

import fitz
import pytest
from conftest import NEW
from test_layers import layer_fixture
from test_pdf import pdf_fixture

EXPECTED = [None, "TROQUEL & DIMENSIONES", "RELIEVE", "ESTAMPADO", "BARNIZ BRILLO", "BARNIZ MATTE"]


def output(helper, pc):
	helper.generate_pdf_for_printcard(printcard=pc.name)
	from conftest import frappe

	return frappe.local.response.filecontent


@pytest.mark.parametrize(
	"width,height,specs",
	[
		(13, 14, 3.75),
		(14, 13, 3.75),
		(18.5, 20, 5.5),
		(20, 18.5, 5.5),
		(24, 26, 7),
		(26, 24, 7),
		(35, 36, 9),
		(36, 35, 9),
	],
)
def test_labels_fit_all_canvas_margins_and_leave_content_unchanged(tmp_path, width, height, specs):
	orientation = "Portrait" if height > width else "Landscape"
	source, pc, cv = pdf_fixture(tmp_path, orientation)
	cv.update(ancho_pdf=width, alto_pdf=height, ancho_specs=specs, margin_top=0.25)
	layer_fixture(source, empty=frozenset())
	helper, manager = NEW["helper.py"], NEW["pdf_manipulator.py"]
	labelled = output(helper, pc)
	with patch.object(manager, "add_separation_label"):
		baseline = output(helper, pc)
	with fitz.open(stream=labelled, filetype="pdf") as pdf, fitz.open(stream=baseline, filetype="pdf") as old:
		assert len(pdf) == len(old) == 6
		for page, previous, label in zip(pdf, old, EXPECTED, strict=True):
			assert page.rect == previous.rect == fitz.Rect(0, 0, width * 72, height * 72)
			assert page.get_drawings() == previous.get_drawings()
			if label:
				assert page.get_text().splitlines()[-1] == label
				bounds = page.search_for(label)[0]
				assert 0 < bounds.y0 < bounds.y1 < cv.margin_top * 72
				assert bounds.x1 == pytest.approx(width * 72 - cv.margin_right * 72, abs=0.01)
				assert bounds.x0 > width * 72 / 2
			else:
				assert page.get_text() == previous.get_text()
			# Exact pixels below the reserved label margin, including template and artwork.
			clip = fitz.Rect(0, cv.margin_top * 72, page.rect.width, page.rect.height)
			assert page.get_pixmap(clip=clip).samples == previous.get_pixmap(clip=clip).samples


@pytest.mark.parametrize("missing", ["TROQUEL", "RELIEVE", "ESTAMPADO", "BARNIZ BRILLO", "BARNIZ MATTE"])
def test_labels_follow_present_layers_instead_of_page_numbers(tmp_path, missing):
	source, pc, _cv = pdf_fixture(tmp_path, "Landscape")
	layer_fixture(source, empty=frozenset({missing}))
	with fitz.open(stream=output(NEW["helper.py"], pc), filetype="pdf") as pdf:
		expected = [
			label
			for label in EXPECTED[1:]
			if label != ("TROQUEL & DIMENSIONES" if missing == "TROQUEL" else missing)
		]
		assert len(pdf) == len(expected) + 1
		assert [page.get_text().splitlines()[-1] for page in list(pdf)[1:]] == expected


def test_source_marker_cannot_label_an_ordinary_pdf(tmp_path):
	from pypdf import PdfReader, PdfWriter
	from pypdf.generic import NameObject, TextStringObject

	source, pc, _cv = pdf_fixture(tmp_path, "Landscape")
	writer = PdfWriter()
	writer.append(PdfReader(source))
	for page in writer.pages:
		page[NameObject("/printcard_separation_label")] = TextStringObject("TROQUEL")
	writer.write(source)
	with fitz.open(stream=output(NEW["helper.py"], pc), filetype="pdf") as pdf:
		assert len(pdf) == 2
		assert all("TROQUEL & DIMENSIONES" not in page.get_text() for page in pdf)


def test_canvas_without_top_margin_does_not_cover_artwork(tmp_path):
	source, pc, cv = pdf_fixture(tmp_path, "Landscape")
	cv.margin_top = 0
	layer_fixture(source)
	result = output(NEW["helper.py"], pc)
	with patch.object(NEW["pdf_manipulator.py"], "add_separation_label"):
		baseline = output(NEW["helper.py"], pc)
	with fitz.open(stream=result, filetype="pdf") as pdf, fitz.open(stream=baseline, filetype="pdf") as old:
		assert len(pdf) == len(old) == 5
		assert all(a.get_pixmap().samples == b.get_pixmap().samples for a, b in zip(pdf, old, strict=True))
