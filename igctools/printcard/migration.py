"""Read-only migration gates. No document, schema, script or file rewrites.

The standard PrintCard schema remains supplied by PowerPro in this phase.
Never uninstall PowerPro as part of this migration.
"""

import hashlib
import importlib.util
import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import frappe

CONTROLLER = "igctools.printcard.controller.PrintCard"
REQUIRED_FIELDS = {
	"archivo": "Attach",
	"printcard_file": "Attach",
	"printcard_file_signed": "Attach",
	"estado": "Select",
	"firma_cliente": "Signature",
	"aprobado": "Check",
}


def source_status():
	manifest = json.loads(Path(__file__).with_name("origin.json").read_text())
	spec = importlib.util.find_spec("powerpro")
	if spec is None:
		return {"available": False, "commit": manifest["commit"], "files": []}
	root = Path(next(iter(spec.submodule_search_locations)))
	files = []
	for expected in manifest["files"].values():
		path = root.parent / expected["source"]
		actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
		files.append({"path": expected["source"], "matches": actual == expected["sha256"]})
	return {"available": True, "commit": manifest["commit"], "files": files}


def assert_source_compatibility():
	"""Fail a migration when the installed source differs from the audited baseline."""
	if not frappe.db.exists("DocType", "PrintCard"):
		return  # IGCTools can still be installed on sites that do not use PrintCard.
	meta = frappe.get_meta("PrintCard")
	for name, fieldtype in REQUIRED_FIELDS.items():
		field = meta.get_field(name)
		if not field or field.fieldtype != fieldtype:
			frappe.throw(f"PrintCard: campo incompatible para la migración: {name}.")
	if "powerpro" not in frappe.get_installed_apps():
		frappe.throw(
			frappe._(
				"PrintCard: esta fase requiere conservar PowerPro instalado y su definición del DocType."
			)
		)
	status = source_status()
	differences = [f["path"] for f in status["files"] if not f["matches"]]
	if not status["available"] or differences:
		frappe.throw(
			frappe._("PrintCard: PowerPro no coincide con la versión auditada; revisar antes de activar. ")
			+ ", ".join(differences)
		)
	# Import before accepting the deployment: the PDF dependencies must already work.
	from igctools.printcard import helper


def verify_activation():
	if not frappe.db.exists("DocType", "PrintCard"):
		return
	from frappe.model.base_document import get_controller

	from igctools import hooks

	controller = get_controller("PrintCard")
	if f"{controller.__module__}.{controller.__name__}" != CONTROLLER:
		frappe.throw(frappe._("PrintCard: otra app está sustituyendo el controlador de IGCTools."))
	for source, target in hooks.override_whitelisted_methods.items():
		if frappe.override_whitelisted_method(source) != target:
			frappe.throw(f"PrintCard: una ruta continúa usando otro motor: {source}.")


@frappe.whitelist()
def status():
	"""Safe operational check, also callable through bench execute."""
	frappe.only_for("System Manager")
	from frappe.model.base_document import get_controller

	from igctools import hooks

	exists = bool(frappe.db.exists("DocType", "PrintCard"))
	controller = get_controller("PrintCard") if exists else None
	packages = {}
	for name in ("pypdf", "PyMuPDF", "WeasyPrint", "Pillow"):
		try:
			packages[name] = version(name)
		except PackageNotFoundError:
			packages[name] = None
	return {
		"controller": f"{controller.__module__}.{controller.__name__}" if controller else None,
		"sources": source_status(),
		"packages": packages,
		"routes": {
			source: frappe.override_whitelisted_method(source)
			for source in hooks.override_whitelisted_methods
		},
		"schema_owner_transferred": False,
		"powerpro_still_required": True,
		"layers_enabled": True,
		"layer_mode": "single_page_with_recognized_layers",
		"legacy_mode": "multi_page_or_no_recognized_layers",
	}
