import base64
import hashlib
import io
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import fitz
import jinja2
import pytest
from conftest import LEGACY, NEW, Bag, frappe, utils
from PIL import Image, ImageDraw


def pdf_fixture(tmp_path, orientation):
	public = tmp_path / "public" / "files"
	public.mkdir(parents=True)
	utils.get_files_path = lambda is_private=False: str(
		tmp_path / ("private" if is_private else "public") / "files"
	)
	source = public / "original.pdf"
	pdf = fitz.open()
	for i, (width, height) in enumerate([(216, 144), (288, 216)]):
		page = pdf.new_page(width=width, height=height)
		page.insert_text((20, 30), f"ARTE ORIGINAL {i + 1}", fontsize=12)
		page.draw_rect(fitz.Rect(20, 40, width - 20, height - 20), color=(1, 0, 0), width=1)
		page.draw_line((20, 50), (width - 20, height - 30), color=(0, 0, 1), width=2)
	pdf.save(source)
	pdf.close()
	w, h = (7, 6) if orientation == "Landscape" else (6, 7)
	cv = Bag(
		name="Test Canvas",
		ancho_pdf=w,
		alto_pdf=h,
		ancho_specs=1,
		orientation=orientation,
		margin_top=0.2,
		margin_bottom=0.2,
		margin_left=0.2,
		margin_right=0.2,
		codigo_html='<div style="font-size:12pt;color:#0a1f41">PRINTCARD {{ doc.cliente }}<br>SKU {{ doc.producto }}</div>',
		codigo_css="body { margin:0; font-family: DejaVu Sans; }",
		signature_x_position=0.1,
		signature_y_position=0.8,
		signature_width=1,
		signature_height=0.3,
		date_x_position=0,
		date_y_position=1,
		date_font_color="#000000",
		font_size=8,
	)
	pc = Bag(
		name="ACME - [PC]A1.v1.1",
		cliente="ACME",
		producto="SKU1",
		archivo="/files/original.pdf",
		save=MagicMock(),
	)
	frappe.get_doc.side_effect = lambda doctype, name: pc if doctype == "PrintCard" else cv
	frappe.get_all.return_value = [cv]
	frappe.db.get_single_value.return_value = 0.1
	frappe.render_template = lambda html, context: jinja2.Template(html).render(**context)
	return source, pc, cv


def compare_documents(first, second):
	with fitz.open(first) as a, fitz.open(second) as b:
		assert len(a) == len(b) == 2
		for pa, pb in zip(a, b, strict=False):
			assert pa.rect == pb.rect
			assert pa.get_text() == pb.get_text()
			assert pa.get_pixmap().samples == pb.get_pixmap().samples
			assert len(pa.get_drawings()) == len(pb.get_drawings())
			assert "ARTE ORIGINAL" in pb.get_text()
			assert "PRINTCARD ACME" in pb.get_text()
			assert len(pb.get_drawings()) >= 2


@pytest.mark.parametrize("orientation", ["Portrait", "Landscape"])
def test_pdf_and_signature_are_visually_identical_on_every_page(tmp_path, orientation):
	source, pc, _cv = pdf_fixture(tmp_path, orientation)
	original_digest = hashlib.sha256(source.read_bytes()).hexdigest()
	outputs = []
	for modules, label in [(LEGACY, "powerpro"), (NEW, "igctools")]:
		with patch.object(modules["helper.py"], "get_unique_filename", return_value=label + ".pdf"):
			url = modules["helper.py"].generate_pdf_for_printcard(printcard=pc.name, pdf_path=True)
		outputs.append(tmp_path / "public" / url.lstrip("/"))
	compare_documents(*outputs)
	assert hashlib.sha256(source.read_bytes()).hexdigest() == original_digest
	image = Image.new("RGBA", (180, 50), (255, 255, 255, 0))
	ImageDraw.Draw(image).line([(5, 40), (40, 8), (60, 35), (110, 15), (170, 30)], fill=(0, 0, 0), width=3)
	buf = io.BytesIO()
	image.save(buf, format="PNG")
	pc.firma_cliente = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
	signed = []
	# Use a non-Administrator to exercise the stable legacy signed filename.
	frappe.session.user = "client@example.test"
	for modules, label, path in [(LEGACY, "powerpro", outputs[0]), (NEW, "igctools", outputs[1])]:
		pc.printcard_file = "/files/" + path.name
		with patch.object(modules["helper.py"], "get_unique_filename", return_value=label + "-signed.pdf"):
			modules["helper.py"]._sign_pdf_with_base64(pc.name)
		signed.append(tmp_path / "public" / pc.printcard_file_signed.lstrip("/"))
	assert pc.save.call_count == 2
	compare_documents(*signed)
	with fitz.open(signed[1]) as doc:
		for page in doc:
			assert "2026-09-19" in page.get_text()
			assert len(page.get_images()) == 1
		if out := os.environ.get("PRINTCARD_QA_DIR"):
			folder = Path(out)
			folder.mkdir(parents=True, exist_ok=True)
			for i, page in enumerate(doc):
				page.get_pixmap(matrix=fitz.Matrix(1, 1)).save(folder / f"{orientation.lower()}-{i + 1}.png")
			(folder / f"{orientation.lower()}-signed.pdf").write_bytes(signed[1].read_bytes())
	assert pc.archivo == "/files/original.pdf"
	assert hashlib.sha256(source.read_bytes()).hexdigest() == original_digest


def test_preview_uses_response_without_mutating_output_fields(tmp_path):
	_source, pc, _cv = pdf_fixture(tmp_path, "Landscape")
	NEW["helper.py"].generate_pdf_for_printcard(printcard=pc.name)
	assert frappe.local.response.type == "pdf"
	with fitz.open(stream=frappe.local.response.filecontent, filetype="pdf") as pdf:
		assert len(pdf) == 2
	assert pc.printcard_file is None
	assert pc.printcard_file_signed is None
	pc.save.assert_not_called()


def test_mixed_page_dimensions_and_canvas_choice_match_baseline(tmp_path):
	source, _, _ = pdf_fixture(tmp_path, "Portrait")
	for modules in (LEGACY, NEW):
		assert modules["pdf_manipulator.py"].get_pdf_dimensions(str(source)) == (4, 3)
		candidates = [
			("large", 20, 18, "Landscape"),
			("small", 6, 5, "Landscape"),
			("too-small", 3, 2, "Landscape"),
		]
		assert modules["pdf_manipulator.py"].select_best_canvas(4, 3, candidates, 0.5)[0] == "small"


def test_missing_canvas_reports_error_without_creating_output(tmp_path):
	source, pc, _ = pdf_fixture(tmp_path, "Landscape")
	frappe.get_all.return_value = []
	assert NEW["helper.py"].generate_pdf_for_printcard(printcard=pc.name, pdf_path=True) is None
	frappe.respond_as_web_page.assert_called_once()
	assert list(source.parent.iterdir()) == [source]
