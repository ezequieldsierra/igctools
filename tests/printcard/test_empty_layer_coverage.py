"""Visible coverage regressions, including unpainted Illustrator soft masks."""

import io

import fitz
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject
from test_layers import rendered, stream

from igctools.printcard.layers import _has_content, prepare_printcard_source


def coverage_page(paint, *, mask_gray=1, rotation=0, width=288, height=216):
	writer = PdfWriter()
	page = writer.add_blank_page(width=width, height=height)
	mask = stream(f"{mask_gray} g 0 0 {width} {height} re f".encode())
	mask.update(
		{
			NameObject("/Subtype"): NameObject("/Form"),
			NameObject("/BBox"): page.mediabox,
			NameObject("/Resources"): DictionaryObject(),
			NameObject("/Group"): DictionaryObject(
				{NameObject("/S"): NameObject("/Transparency"), NameObject("/CS"): NameObject("/DeviceGray")}
			),
		}
	)
	page[NameObject("/Resources")] = DictionaryObject(
		{
			NameObject("/ExtGState"): DictionaryObject(
				{
					NameObject("/Mask"): DictionaryObject(
						{
							NameObject("/SMask"): DictionaryObject(
								{
									NameObject("/S"): NameObject("/Luminosity"),
									NameObject("/G"): writer._add_object(mask),
								}
							)
						}
					),
					NameObject("/Clear"): DictionaryObject(
						{NameObject("/ca"): FloatObject(0), NameObject("/CA"): FloatObject(0)}
					),
				}
			)
		}
	)
	page[NameObject("/Rotate")] = NumberObject(rotation)
	page[NameObject("/Contents")] = writer._add_object(stream(paint))
	return page


@pytest.mark.parametrize(
	"paint,expected",
	[
		(b"q /Mask gs Q", False),  # mask draws, but the page never paints with it
		(b"q /Mask gs 10 10 30 30 re n Q", False),
		(b"q /Mask gs 0 0 0 0 re f Q", False),
		(b"q /Clear gs 10 10 30 30 re f Q", False),
		(b"q 0 0 5 5 re W n 20 20 30 30 re f Q", False),
		(b"500 500 20 20 re f", False),
		(b"1 g 10 10 30 30 re f", True),  # white is still real artwork
		(b"q /Mask gs 10 10 30 30 re f Q", True),
		(b"0 G 0 w 10 10 m 200 190 l S", True),  # hairline
		(b"0 g 10 10 0.1 0.1 re f", True),  # small finishing element
	],
)
@pytest.mark.parametrize("rotation", [0, 90])
def test_only_effective_paint_counts(paint, expected, rotation):
	assert _has_content(coverage_page(paint, rotation=rotation)) is expected


def test_fully_masked_artwork_is_empty():
	assert not _has_content(coverage_page(b"q /Mask gs 10 10 30 30 re f Q", mask_gray=0))


@pytest.mark.parametrize("rotation", [0, 90])
def test_coverage_checks_later_tiles_and_crop(rotation):
	page = coverage_page(b"0 g 800 800 10 10 re f", width=1200, height=1200, rotation=rotation)
	assert _has_content(page)
	page[NameObject("/CropBox")] = ArrayObject([NumberObject(n) for n in [100, 100, 700, 700]])
	assert not _has_content(page)


@pytest.mark.parametrize("populated", [False, True])
def test_unused_masks_cannot_create_optional_pages(tmp_path, populated):
	paint = b"/Layer << /Title (ARTE) >> BDC q /Mask gs 10 10 30 30 re f Q EMC "
	paint += b"/Layer << /Title (TROQUEL) >> BDC 0 0 1 RG 2 w 5 5 200 160 re S EMC "
	for label in ["RELIEVE", "ESTAMPADO", "BARNIZ BRILLO", "BARNIZ MATTE"]:
		paint += f"/Layer << /Title ({label}) >> BDC ".encode()
		if populated and label == "RELIEVE":
			paint += b"1 g 50 50 20 20 re f "
		else:
			paint += b"q /Mask gs 0 0 0 0 re f Q "
		paint += b"EMC "
	page = coverage_page(paint)
	writer = PdfWriter()
	writer.add_page(page)
	source = tmp_path / "masked.pdf"
	writer.write(source)
	result = prepare_printcard_source(source)
	assert [p.printcard_separation_label for p in result.pages] == (
		["ARTE + TROQUEL + PRESERVADO", "TROQUEL", "RELIEVE"]
		if populated
		else ["ARTE + TROQUEL + PRESERVADO", "TROQUEL"]
	)
	if populated:
		with rendered(result) as pdf:
			assert any(d["type"] == "f" and d["fill"] == (1, 1, 1) for d in pdf[2].get_drawings())
			assert any(d["type"] == "s" for d in pdf[2].get_drawings())


def test_bbox_commands_are_not_evidence_of_painted_content():
	page = coverage_page(b"q /Mask gs 0 0 0 0 re f Q")
	writer = PdfWriter()
	writer.add_page(page)
	buffer = io.BytesIO()
	writer.write(buffer)
	with fitz.open(stream=buffer.getvalue(), filetype="pdf") as pdf:
		assert any(kind != "ignore-text" for kind, *_ in pdf[0].get_bboxlog())
	assert not _has_content(PdfReader(buffer).pages[0])
