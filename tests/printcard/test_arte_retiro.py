"""Optional reverse artwork is isolated from the front and precedes technical pages."""

import hashlib
from unittest.mock import patch

import fitz
import pytest
from conftest import NEW
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, FloatObject, NameObject, TextStringObject
from test_layers import layer_fixture, rendered, stream
from test_page_labels import output
from test_pdf import pdf_fixture

from igctools.printcard.layers import prepare_printcard_source


def retirement_fixture(path, mode="paint", kind="illustrator", label="ARTE RETIRO"):
	layer_fixture(path)
	reader = PdfReader(path)
	writer = PdfWriter()
	writer.append(reader)
	page = writer.pages[0]
	resources = page["/Resources"]
	properties = resources["/Properties"]
	prop = DictionaryObject({NameObject("/Name" if kind == "ocg" else "/Title"): TextStringObject(label)})
	if kind == "ocg":
		prop[NameObject("/Type")] = NameObject("/OCG")
	properties[NameObject("/Retiro")] = writer._add_object(prop)
	paint = {
		"paint": b"q 0 1 0 rg 250 100 25 25 re f Q",
		"white": b"q 1 1 1 rg 250 100 25 25 re f Q",
		"clip": b"q 250 100 25 25 re W n Q",
		"offpage": b"q 0 1 0 rg 500 500 25 25 re f Q",
		"transparent": b"q /Clear gs 250 100 25 25 re f Q",
	}[mode]
	resources[NameObject("/ExtGState")] = DictionaryObject(
		{NameObject("/Clear"): DictionaryObject({NameObject("/ca"): FloatObject(0)})}
	)
	if kind == "hidden":
		hidden = DictionaryObject(
			{
				NameObject("/AIType"): NameObject("/HiddenLayer"),
				NameObject("/Contents"): writer._add_object(stream(paint)),
				NameObject("/Resources"): DictionaryObject(),
			}
		)
		properties[NameObject("/HiddenRetiro")] = writer._add_object(hidden)
		paint = b"/AltAI8 /HiddenRetiro BDC EMC"
	elif kind == "nested":
		paint = b"/Layer << /Title (Subgrupo) >> BDC " + paint + b" EMC"
	tag = b"/OC" if kind == "ocg" else b"/Layer"
	content = page.get_contents().get_data() + b"\n" + tag + b" /Retiro BDC " + paint + b" EMC\n"
	page[NameObject("/Contents")] = writer._add_object(stream(content))
	writer.write(path)
	return path


@pytest.mark.parametrize("kind", ["illustrator", "ocg", "hidden", "nested"])
@pytest.mark.parametrize("label", ["ARTE RETIRO", "  2. Arte   Retiro  "])
def test_interior_is_second_and_does_not_leak_to_front(tmp_path, kind, label):
	source = retirement_fixture(tmp_path / "source.pdf", kind=kind, label=label)
	digest = hashlib.sha256(source.read_bytes()).digest()
	result = prepare_printcard_source(source)
	assert [item.title for item in result.outline][:3] == [
		"ARTE + TROQUEL + PRESERVADO",
		"ARTE RETIRO + TROQUEL",
		"TROQUEL + DIMENSIONES",
	]
	with rendered(result) as pdf:
		assert len(pdf) == 6
		assert [round(d["rect"].x0) for d in pdf[0].get_drawings()] == [10, 40, 70]
		assert [round(d["rect"].x0) for d in pdf[1].get_drawings()] == [250, 70]
		assert [round(d["rect"].x0) for d in pdf[2].get_drawings()] == [70, 220]
		assert all(page.rect == fitz.Rect(0, 0, 288, 216) for page in pdf)
	assert hashlib.sha256(source.read_bytes()).digest() == digest


@pytest.mark.parametrize("mode", ["absent", "clip", "offpage", "transparent", "white"])
def test_empty_or_absent_interior_does_not_create_a_page(tmp_path, mode):
	source = tmp_path / "source.pdf"
	if mode == "absent":
		layer_fixture(source)
	else:
		retirement_fixture(source, mode=mode)
	result = prepare_printcard_source(source)
	assert len(result.pages) == (6 if mode == "white" else 5)
	assert any("ARTE RETIRO" in item.title for item in result.outline) == (mode == "white")


def test_interior_canvas_badge_and_vector_artwork(tmp_path):
	source, pc, cv = pdf_fixture(tmp_path, "Landscape")
	retirement_fixture(source)
	helper, manager = NEW["helper.py"], NEW["pdf_manipulator.py"]
	labelled = output(helper, pc)
	with patch.object(manager, "add_separation_label"):
		baseline = output(helper, pc)
	with fitz.open(stream=labelled, filetype="pdf") as pdf, fitz.open(stream=baseline, filetype="pdf") as old:
		assert len(pdf) == 6
		assert [p.get_text().splitlines()[-1] for p in list(pdf)[:3]] == [
			"ARTE & TROQUEL",
			"ARTE RETIRO & TROQUEL",
			"TROQUEL & DIMENSIONES",
		]
		for page, previous in zip(pdf, old, strict=True):
			clip = fitz.Rect(0, cv.margin_top * 72, page.rect.width, page.rect.height)
			assert page.get_pixmap(clip=clip).samples == previous.get_pixmap(clip=clip).samples


def test_pdf_without_retiro_is_pixel_identical_to_existing_order(tmp_path):
	source = layer_fixture(tmp_path / "source.pdf")
	result = prepare_printcard_source(source)
	with rendered(result) as pdf:
		assert len(pdf) == 5
		assert [[round(d["rect"].x0) for d in p.get_drawings()] for p in pdf] == [
			[10, 40, 70],
			[70, 220],
			[100, 70],
			[160, 70],
			[190, 70],
		]
