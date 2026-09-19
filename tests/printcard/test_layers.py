"""Synthetic layer fixtures only; never publish customer PDFs in the repository."""

import base64
import hashlib
import io
from unittest.mock import patch

import fitz
import pytest
from conftest import NEW, frappe
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
	ArrayObject,
	BooleanObject,
	DecodedStreamObject,
	DictionaryObject,
	NameObject,
	NumberObject,
	TextStringObject,
)
from test_pdf import pdf_fixture

from igctools.printcard.layers import LayerSeparationError, prepare_printcard_source


def stream(data):
	result = DecodedStreamObject()
	result.set_data(data)
	return result


def layer_fixture(path, empty=frozenset({"ESTAMPADO"}), ocg=False, nested=False):
	writer = PdfWriter()
	page = writer.add_blank_page(width=288, height=216)
	properties, xobjects = DictionaryObject(), DictionaryObject()
	resources = DictionaryObject({NameObject("/Properties"): properties, NameObject("/XObject"): xobjects})
	page[NameObject("/Resources")] = resources
	content = []
	for i, label in enumerate(
		[
			"ARTE",
			"PRESERVADO",
			"TROQUEL",
			"RELIEVE",
			"ESTAMPADO",
			"BARNIZ BRILLO",
			"BARNIZ MATTE",
			"DIMENSIONES",
		]
	):
		key = NameObject(f"/L{i}")
		prop = DictionaryObject({NameObject("/Name" if ocg else "/Title"): TextStringObject(label)})
		if ocg:
			prop[NameObject("/Type")] = NameObject("/OCG")
		properties[key] = writer._add_object(prop)
		hidden = i >= 3 and not ocg
		prop[NameObject("/Visible")] = BooleanObject(not hidden)
		paint = b"q 1 0 0 rg " + f"{10 + i * 30} 30 20 20 re f Q\n".encode()
		if label in empty:
			paint = b"q 1 0 0 rg 0 0 10 10 re W n Q\n"  # clipping does not count as artwork
		content.append((b"/OC " if ocg else b"/Layer ") + str(key).encode() + b" BDC\n")
		if hidden:
			# Own resources deliberately collide with the outer names.
			hkey = NameObject(f"/H{i}")
			paint = b"/Local Do\n" if label not in empty else paint
			form = stream(f"q 0 0 1 rg {10 + i * 30} 30 20 20 re f Q".encode())
			form.update({NameObject("/Subtype"): NameObject("/Form"), NameObject("/BBox"): page.mediabox})
			properties[hkey] = DictionaryObject(
				{
					NameObject("/AIType"): NameObject("/HiddenLayer"),
					NameObject("/Contents"): writer._add_object(stream(paint)),
					NameObject("/Resources"): DictionaryObject(
						{
							NameObject("/XObject"): DictionaryObject(
								{NameObject("/Local"): writer._add_object(form)}
							)
						}
					),
				}
			)
			content.append(b"/AltAI8 " + str(hkey).encode() + b" BDC EMC\n")
		elif nested:
			content.append(b"/Layer << /Title (Subgrupo) >> BDC " + paint + b" EMC\n")
		else:
			content.append(paint)
		content.append(b"EMC\n")
	page[NameObject("/Contents")] = writer._add_object(stream(b"".join(content)))
	writer.write(path)
	return path


def rendered(reader):
	writer = PdfWriter()
	writer.append(reader)
	buffer = io.BytesIO()
	writer.write(buffer)
	return fitz.open(stream=buffer.getvalue(), filetype="pdf")


@pytest.mark.parametrize("ocg,nested", [(False, False), (False, True), (True, False), (True, True)])
def test_layer_order_hidden_content_and_empty_omission(tmp_path, ocg, nested):
	source = layer_fixture(tmp_path / "source.pdf", ocg=ocg, nested=nested)
	digest = hashlib.sha256(source.read_bytes()).digest()
	result = prepare_printcard_source(source)
	assert [item.title for item in result.outline] == [
		"ARTE + TROQUEL + PRESERVADO",
		"TROQUEL",
		"RELIEVE",
		"BARNIZ BRILLO",
		"BARNIZ MATTE",
	]
	with rendered(result) as pdf:
		assert len(pdf) == 5
		# Each layer has an independent X coordinate, exposing any content leakage.
		assert [[round(d["rect"].x0) for d in p.get_drawings()] for p in pdf] == [
			[10, 40, 70],
			[70],
			[100],
			[160],
			[190],
		]
		assert all(p.rect == fitz.Rect(0, 0, 288, 216) for p in pdf)
		if not ocg:
			assert pdf[2].get_drawings()[0]["fill"] == (0, 0, 1)
	assert hashlib.sha256(source.read_bytes()).digest() == digest


@pytest.mark.parametrize(
	"empty,expected",
	[
		(frozenset(), 6),
		(frozenset({"TROQUEL", "RELIEVE", "ESTAMPADO", "BARNIZ BRILLO", "BARNIZ MATTE"}), 1),
		(frozenset({"RELIEVE"}), 5),
	],
)
def test_only_populated_optional_pages_are_created(tmp_path, empty, expected):
	assert len(prepare_printcard_source(layer_fixture(tmp_path / "source.pdf", empty)).pages) == expected


def test_multipage_and_flattened_pdf_keep_original_content(tmp_path):
	source = layer_fixture(tmp_path / "layers.pdf")
	reader = PdfReader(source)
	writer = PdfWriter()
	writer.add_page(reader.pages[0])
	writer.add_page(reader.pages[0])
	writer.write(tmp_path / "multi.pdf")
	result = prepare_printcard_source(tmp_path / "multi.pdf")
	assert len(result.pages) == 2
	assert result.pages[0].get_contents().get_data() == reader.pages[0].get_contents().get_data()
	with fitz.open() as pdf:
		page = pdf.new_page()
		page.insert_text((20, 30), "Flat artwork")
		pdf.save(tmp_path / "flat.pdf")
	result = prepare_printcard_source(tmp_path / "flat.pdf")
	assert (
		result.pages[0].get_contents().get_data()
		== PdfReader(tmp_path / "flat.pdf").pages[0].get_contents().get_data()
	)


def test_layered_annotations_are_rejected_instead_of_silently_lost(tmp_path):
	source = layer_fixture(tmp_path / "source.pdf")
	writer = PdfWriter()
	writer.append(PdfReader(source))
	writer.pages[0][NameObject("/Annots")] = ArrayObject(
		[DictionaryObject({NameObject("/Subtype"): NameObject("/Text")})]
	)
	writer.write(tmp_path / "annotated.pdf")
	with pytest.raises(LayerSeparationError, match="anotaciones"):
		prepare_printcard_source(tmp_path / "annotated.pdf")


def test_layer_pages_pass_through_existing_canvas_and_signature(tmp_path):
	source, pc, _cv = pdf_fixture(tmp_path, "Landscape")
	layer_fixture(source)
	digest = hashlib.sha256(source.read_bytes()).digest()
	helper = NEW["helper.py"]
	with patch.object(helper, "get_unique_filename", return_value="layer-card.pdf"):
		pc.printcard_file = helper.generate_pdf_for_printcard(printcard=pc.name, pdf_path=True)
	with fitz.open(source.parent / "layer-card.pdf") as pdf:
		assert len(pdf) == 5
		assert all("PRINTCARD ACME" in p.get_text() for p in pdf)
		assert [len(p.get_drawings()) for p in pdf] == [3, 1, 1, 1, 1]
	image = Image.new("RGBA", (20, 10), (0, 0, 0, 255))
	buffer = io.BytesIO()
	image.save(buffer, format="PNG")
	pc.firma_cliente = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
	frappe.session.user = "qa@example.test"
	with patch.object(helper, "get_unique_filename", return_value="layer-signed.pdf"):
		helper._sign_pdf_with_base64(pc.name)
	with fitz.open(source.parent / "layer-signed.pdf") as pdf:
		assert len(pdf) == 5
		assert all(len(p.get_images()) == 1 and "2026-09-19" in p.get_text() for p in pdf)
	assert pc.archivo == "/files/original.pdf"
	assert pc.printcard_file_signed == "/files/layer-signed.pdf"
	assert hashlib.sha256(source.read_bytes()).digest() == digest
