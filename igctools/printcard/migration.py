"""Read-only migration gates. No document, schema, script or file rewrites.

The standard PrintCard schema remains supplied by PowerPro in this phase.
Never uninstall PowerPro as part of this migration.
"""

from importlib.metadata import PackageNotFoundError, version

import frappe

from igctools.printcard.compatibility import CONTROLLER, METHOD_OVERRIDES, source_status

REQUIRED_FIELDS = {
	"archivo": "Attach",
	"printcard_file": "Attach",
	"printcard_file_signed": "Attach",
	"estado": "Select",
	"firma_cliente": "Signature",
	"aprobado": "Check",
}


def assert_source_compatibility():
	"""Validate activation, or keep the existing engine when its source is unknown."""
	if not frappe.db.exists("DocType", "PrintCard"):
		return  # IGCTools can still be installed on sites that do not use PrintCard.
	sources = source_status()
	if not sources["compatible"]:
		# Hashes still block the new runtime, not unrelated site schema migrations.
		# Refuse stale/mixed hooks before allowing the old runtime to continue.
		verify_activation()
		print("PrintCard: se conserva PowerPro; el motor IGCTools requiere auditar la fuente instalada.")
		return
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
	# Import before accepting the deployment: the PDF dependencies must already work.
	from igctools.printcard import helper


def verify_activation():
	if not frappe.db.exists("DocType", "PrintCard"):
		return
	from frappe.model.base_document import get_controller

	controller = get_controller("PrintCard")
	if not source_status()["compatible"]:
		if controller.__module__.startswith("igctools.printcard"):
			frappe.throw(
				frappe._("PrintCard: reiniciar procesos; un controlador IGCTools incompatible sigue activo.")
			)
		for source in METHOD_OVERRIDES:
			if frappe.override_whitelisted_method(source).startswith("igctools.printcard."):
				frappe.throw(
					frappe._("PrintCard: reiniciar procesos; una ruta IGCTools incompatible sigue activa.")
				)
		conditions = frappe.get_hooks("permission_query_conditions").get("PrintCard", [])
		if "igctools.printcard.permissions.printcard_query_conditions" in conditions:
			frappe.throw(
				frappe._("PrintCard: reiniciar procesos; un filtro IGCTools incompatible sigue activo.")
			)
		return
	if f"{controller.__module__}.{controller.__name__}" != CONTROLLER:
		frappe.throw(frappe._("PrintCard: otra app está sustituyendo el controlador de IGCTools."))
	for source, target in METHOD_OVERRIDES.items():
		if frappe.override_whitelisted_method(source) != target:
			frappe.throw(f"PrintCard: una ruta continúa usando otro motor: {source}.")


@frappe.whitelist()
def status():
	"""Safe operational check, also callable through bench execute."""
	frappe.only_for("System Manager")
	from frappe.model.base_document import get_controller

	exists = bool(frappe.db.exists("DocType", "PrintCard"))
	controller = get_controller("PrintCard") if exists else None
	controller_path = f"{controller.__module__}.{controller.__name__}" if controller else None
	sources = source_status()
	active = controller_path == CONTROLLER and sources["compatible"]
	packages = {}
	for name in ("pypdf", "PyMuPDF", "WeasyPrint", "Pillow"):
		try:
			packages[name] = version(name)
		except PackageNotFoundError:
			packages[name] = None
	return {
		"controller": controller_path,
		"sources": sources,
		"packages": packages,
		"routes": {source: frappe.override_whitelisted_method(source) for source in METHOD_OVERRIDES},
		"schema_owner_transferred": False,
		"powerpro_still_required": True,
		"runtime_enabled": active,
		"activation_blocked": exists and not sources["compatible"],
		"layers_enabled": active,
		"layer_mode": "single_page_with_recognized_layers" if active else None,
		"legacy_mode": "multi_page_or_no_recognized_layers" if active else "all_pdfs",
	}
