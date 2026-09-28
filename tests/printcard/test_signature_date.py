import base64
import hashlib
import io
import json
from pathlib import Path
from unittest.mock import MagicMock

import fitz
import pytest
from conftest import NEW, Bag, frappe, utils
from PIL import Image, ImageDraw

from igctools.printcard import regeneration
from igctools.printcard.signature_date import original_signature_date, validate_signature_date


def footer_pdf(path, dates, labels=True, extra=None):
	with fitz.open() as pdf:
		for value in dates:
			page = pdf.new_page(width=360, height=300)
			page.insert_text((40, 40), "ARTE ORIGINAL 2024-01-01")
			if labels:
				page.insert_text((60, 210), "FECHA", fontsize=9)
				page.insert_text((200, 210), "FIRMA", fontsize=9)
			if value:
				page.insert_text((40, 195), value, fontsize=12)
			if extra:
				page.insert_text((40, 175), extra, fontsize=12)
		pdf.save(path)
	return path


def test_original_date_is_read_from_every_footer_not_artwork_or_metadata(tmp_path):
	path = footer_pdf(tmp_path / "old.pdf", ["2025-03-07", "2025-03-07"])
	assert original_signature_date(str(path)) == "2025-03-07"


@pytest.mark.parametrize(
	"dates,labels,extra",
	[
		([None], True, None),
		(["2025-03-07"], False, None),
		(["2025-03-07", "2025-03-08"], True, None),
		(["2025-03-07", None], True, None),
		(["2025-03-07"], True, "2025-03-08"),
		(["2025-02-30"], True, None),
	],
)
def test_unverifiable_dates_fail_closed(tmp_path, dates, labels, extra):
	with pytest.raises(ValueError):
		original_signature_date(str(footer_pdf(tmp_path / "old.pdf", dates, labels, extra)))


@pytest.mark.parametrize("value", ["", "today", "2025-2-1", "2025-02-30", None])
def test_invalid_explicit_date_is_not_replaced_by_today(value):
	with pytest.raises(ValueError):
		validate_signature_date(value)


def signature_image():
	image = Image.new("RGBA", (180, 50), (255, 255, 255, 0))
	ImageDraw.Draw(image).line([(5, 40), (40, 8), (60, 35), (110, 15), (170, 30)], fill="blue", width=3)
	buffer = io.BytesIO()
	image.save(buffer, format="PNG")
	return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


@pytest.fixture
def existing_card(tmp_path, monkeypatch):
	public = tmp_path / "public" / "files"
	private = tmp_path / "private" / "files"
	public.mkdir(parents=True)
	private.mkdir(parents=True)
	monkeypatch.setattr(
		utils,
		"get_files_path",
		lambda is_private=False: str(private if is_private else public),
		raising=False,
	)
	unsigned = footer_pdf(public / "unsigned.pdf", [None, None])
	signed = footer_pdf(public / "signed.pdf", ["2025-03-07", "2025-03-07"])
	canvas = Bag(
		name="Canvas",
		signature_x_position=200 / 72,
		signature_y_position=150 / 72,
		signature_width=90 / 72,
		signature_height=50 / 72,
		date_x_position=160 / 72,
		date_y_position=50 / 45,
		font_size=12,
		date_font_color="#000000",
	)
	pc = Bag(
		name="ACME - [PC]A1.v1.1",
		cliente="ACME",
		archivo="/files/unsigned.pdf",
		printcard_file="/files/unsigned.pdf",
		printcard_file_signed="/files/signed.pdf",
		firma_cliente=signature_image(),
		estado="Aprobado",
		version=1,
		version_arte_interna=1,
		modified="2025-03-10 11:00:00",
		usuarios_asignados=[{"user": "client@example.test"}],
		save=MagicMock(),
		check_permission=MagicMock(),
	)
	pc.as_dict = lambda: {k: v for k, v in pc.items() if not callable(v)}
	frappe.get_doc.side_effect = lambda dt, name: pc if dt == "PrintCard" else canvas
	monkeypatch.setattr(regeneration.helper, "get_best_canvas", lambda *a, **kw: "Canvas")
	return pc, canvas, unsigned, signed, private


def test_ordinary_resigning_keeps_existing_date(existing_card):
	pc, _canvas, _unsigned, old_signed, _private = existing_card
	original_bytes = old_signed.read_bytes()
	NEW["helper.py"]._sign_pdf_with_base64(pc.name)
	assert original_signature_date(NEW["helper.py"].get_file_path(pc.printcard_file_signed)) == "2025-03-07"
	assert old_signed.read_bytes() == original_bytes


def test_first_signature_uses_today(existing_card):
	pc, *_ = existing_card
	pc.printcard_file_signed = None
	NEW["helper.py"]._sign_pdf_with_base64(pc.name)
	assert original_signature_date(NEW["helper.py"].get_file_path(pc.printcard_file_signed)) == utils.today()


def test_ordinary_resigning_does_not_save_if_original_date_is_missing(existing_card):
	pc, _canvas, _unsigned, signed, _private = existing_card
	signed.unlink()
	with pytest.raises(Exception):
		NEW["helper.py"]._sign_pdf_with_base64(pc.name)
	pc.save.assert_not_called()
	assert pc.printcard_file_signed == "/files/signed.pdf"


def test_bulk_preview_is_read_only(existing_card, monkeypatch):
	pc, _canvas, _unsigned, _signed, private = existing_card
	render = MagicMock()
	monkeypatch.setattr(regeneration, "_render_bytes", render)
	result = regeneration.regenerate_printcard_preserving_signature(pc.name)
	assert result["signature_date"] == "2025-03-07" and result["dry_run"]
	assert not list(private.iterdir())
	render.assert_not_called()
	frappe.db.set_value.assert_not_called()
	pc.save.assert_not_called()


def test_regeneration_preserves_originals_date_and_business_fields(existing_card, monkeypatch):
	pc, _canvas, unsigned, signed, _private = existing_card
	snapshot = pc.as_dict()
	old_bytes = {p: p.read_bytes() for p in (unsigned, signed)}
	monkeypatch.setattr(regeneration, "_render_bytes", lambda name: old_bytes[unsigned])
	result = regeneration.regenerate_printcard_preserving_signature(pc.name, dry_run=0)
	assert result["signature_date"] == "2025-03-07" and result["pages"] == 2
	assert pc.as_dict() == snapshot
	pc.save.assert_not_called()
	frappe.only_for.assert_called_once_with("System Manager")
	pc.check_permission.assert_called_once_with("write")
	frappe.db.set_value.assert_called_once_with("PrintCard", pc.name, result["files"], update_modified=False)
	for path, content in old_bytes.items():
		assert path.read_bytes() == content
	manifest = json.loads((Path(result["backup"]) / "manifest.json").read_text())
	assert manifest["signature_date"] == "2025-03-07"
	for field, info in manifest["files"].items():
		assert (
			hashlib.sha256((Path(result["backup"]) / info["file"]).read_bytes()).hexdigest() == info["sha256"]
		)
		assert info["url"] == snapshot[field]
	new_signed = regeneration.helper.get_file_path(result["files"]["printcard_file_signed"])
	assert original_signature_date(new_signed) == "2025-03-07"
	with fitz.open(new_signed) as pdf:
		assert all(utils.today() not in p.get_text() and len(p.get_images()) == 1 for p in pdf)


def test_failed_render_leaves_links_and_originals_untouched(existing_card, monkeypatch):
	pc, _canvas, unsigned, signed, _private = existing_card
	old_bytes = {p: p.read_bytes() for p in (unsigned, signed)}
	monkeypatch.setattr(regeneration, "_render_bytes", MagicMock(side_effect=RuntimeError("render failed")))
	with pytest.raises(RuntimeError, match="render failed"):
		regeneration.regenerate_printcard_preserving_signature(pc.name, dry_run=0)
	frappe.db.set_value.assert_not_called()
	assert set(unsigned.parent.iterdir()) == set(old_bytes)
	for path, content in old_bytes.items():
		assert path.read_bytes() == content


def test_incomplete_signature_is_left_pending(existing_card):
	pc, *_ = existing_card
	pc.printcard_file_signed = None
	with pytest.raises(ValueError, match="pendiente"):
		regeneration.regenerate_printcard_preserving_signature(pc.name, dry_run=0)
	frappe.db.set_value.assert_not_called()


def test_unsigned_cards_are_not_signed_during_regeneration(existing_card, monkeypatch):
	pc, _canvas, unsigned, _signed, _private = existing_card
	pc.printcard_file_signed = None
	pc.firma_cliente = None
	pc.estado = "Pendiente"
	monkeypatch.setattr(regeneration, "_render_bytes", lambda name: unsigned.read_bytes())
	result = regeneration.regenerate_printcard_preserving_signature(pc.name, dry_run=0)
	assert result["signature_date"] is None
	assert set(result["files"]) == {"printcard_file"}


def test_preview_response_is_restored_on_render_error(monkeypatch):
	response = frappe.local.response
	monkeypatch.setattr(
		regeneration.helper, "generate_pdf_for_printcard", MagicMock(side_effect=RuntimeError("failed"))
	)
	with pytest.raises(RuntimeError):
		regeneration._render_bytes("test")
	assert frappe.local.response is response
