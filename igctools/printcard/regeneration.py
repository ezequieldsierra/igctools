"""Administrative PDF refresh with immutable backups and original signing dates."""

import hashlib
import json
import shutil
import uuid
from pathlib import Path

import fitz
import frappe
from frappe.utils import cint

from igctools.printcard import helper, signature_helper
from igctools.printcard.signature_date import original_signature_date


def _render_bytes(name):
	# The established preview renderer returns the complete layered PDF without
	# overwriting a file. Isolate its download response from this JSON API response.
	response = frappe.local.response
	try:
		frappe.local.response = frappe._dict()
		helper.generate_pdf_for_printcard(printcard=name)
		content = frappe.local.response.get("filecontent")
		if not content:
			raise ValueError("No se pudo generar el PDF del PrintCard.")
		return content
	finally:
		frappe.local.response = response


def _new_url(pc, signed=False):
	previous = pc.printcard_file_signed if signed else pc.printcard_file
	private = bool(previous and previous.startswith("/private/files/"))
	folder = "/private/files/" if private else "/files/"
	stem = Path(helper.get_unique_filename(pc.name, pc.cliente)).stem
	suffix = "_firmado" if signed else ""
	return f"{folder}{stem}_regenerado_{uuid.uuid4().hex}{suffix}.pdf"


def _backup(pc, paths, signed_on):
	root = Path(frappe.utils.get_files_path(is_private=True))
	folder = root / f"printcard-backup-{uuid.uuid4().hex}"
	folder.mkdir(mode=0o700)
	manifest = {"printcard": pc.as_dict(), "signature_date": signed_on, "files": {}}
	for field, path in paths.items():
		backup = folder / f"{field}.pdf"
		shutil.copy2(path, backup)
		manifest["files"][field] = {
			"url": pc.get(field),
			"file": backup.name,
			"sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
		}
	(folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, default=str, indent=2))
	return str(folder)


@frappe.whitelist(methods=["POST"])
def regenerate_printcard_preserving_signature(printcard_id: str, dry_run=1):
	"""Preview by default; rebuild one record without save hooks or approval changes.

	Only System Managers may use this maintenance operation. Originals remain at
	their URLs and are also copied with a full record snapshot to a private backup.
	Missing or ambiguous historical dates abort before creating or linking new PDFs.
	The caller owns the transaction, allowing small resumable maintenance batches.
	"""
	frappe.only_for("System Manager")
	preview = bool(cint(dry_run))
	if not preview:
		# Serialize regeneration against ordinary document saves and another batch.
		frappe.db.sql("SELECT name FROM `tabPrintCard` WHERE name=%s FOR UPDATE", (printcard_id,))
	pc = frappe.get_doc("PrintCard", printcard_id)
	pc.check_permission("write")
	paths = {}
	for field in ("printcard_file", "printcard_file_signed"):
		if pc.get(field):
			path = Path(helper.get_file_path(pc.get(field)))
			if not path.is_file():
				raise ValueError(f"Falta el archivo original de {field}; el PrintCard queda pendiente.")
			paths[field] = path
	if bool(pc.get("firma_cliente")) != bool(pc.get("printcard_file_signed")):
		raise ValueError(
			"La firma y el PDF firmado original no están completos; el PrintCard queda pendiente."
		)
	if pc.estado in {"Aprobado", "Reemplazado"} and not pc.get("printcard_file_signed"):
		raise ValueError("Falta el PDF firmado original del PrintCard aprobado.")
	signed_on = (
		original_signature_date(str(paths["printcard_file_signed"])) if pc.printcard_file_signed else None
	)
	source = helper.get_file_path(pc.archivo)
	width, height = helper.pdf_manager.get_pdf_dimensions(source)
	canvas = helper.get_canvas(helper.get_best_canvas(width, height, raise_if_empty=True))
	result = {"printcard": pc.name, "signature_date": signed_on, "canvas": canvas.name, "dry_run": preview}
	if preview:
		return result
	result["backup"] = _backup(pc, paths, signed_on)
	created = []
	try:
		unsigned_url = _new_url(pc)
		unsigned_path = Path(helper.get_file_path(unsigned_url))
		with unsigned_path.open("xb") as output:
			created.append(unsigned_path)
			output.write(_render_bytes(pc.name))
		with fitz.open(unsigned_path) as pdf:
			pages = len(pdf)
			if not pages:
				raise ValueError("El PDF regenerado no tiene páginas.")
		updates = {"printcard_file": unsigned_url}
		if signed_on:
			signed_url = _new_url(pc, signed=True)
			signed_path = Path(helper.get_file_path(signed_url))
			created.append(signed_path)
			ok = signature_helper.sign_pdf_with_base64(
				pdf_path=str(unsigned_path),
				base64_signature=pc.firma_cliente,
				output_path=str(signed_path),
				x=canvas.signature_x_position * 72,
				y=canvas.signature_y_position * 72,
				width=canvas.signature_width * 72,
				height=canvas.signature_height * 72,
				date_x_pos=canvas.date_x_position,
				date_y_pos=canvas.date_y_position,
				date_size=canvas.font_size,
				date_color=helper.convert_hex_to_rgb(canvas.date_font_color),
				signature_date=signed_on,
			)
			if not ok or original_signature_date(str(signed_path)) != signed_on:
				raise ValueError("No se pudo verificar la fecha original en el PDF regenerado.")
			with fitz.open(signed_path) as pdf:
				if len(pdf) != pages:
					raise ValueError("El PDF firmado no conserva todas las páginas regeneradas.")
			updates["printcard_file_signed"] = signed_url
		# Do not call save(): it runs SVG, 3D, assignment and notification hooks.
		# Only the file links change; modified, versions, approvers and status stay intact.
		frappe.db.set_value("PrintCard", pc.name, updates, update_modified=False)
		result.update({"files": updates, "pages": pages})
		return result
	except Exception:
		for path in created:
			path.unlink(missing_ok=True)
		raise
