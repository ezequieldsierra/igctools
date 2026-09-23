"""Deny customer access to internal PrintCards, including direct document URLs."""

import frappe

VISIBLE_STATES = ("Pendiente", "Aprobado", "Rechazado")


def query_conditions(user=None, doctype=None):
	user = user or frappe.session.user
	if user == "Guest":
		return "1=0"
	if frappe.db.get_value("User", user, "user_type") != "Website User":
		return ""
	return "`tabPrintCard`.`estado` IN ('Pendiente', 'Aprobado', 'Rechazado')"


def has_permission(doc, user=None, ptype=None):
	"""Deny only; Frappe still enforces roles, User Permissions and other hooks."""
	user = user or frappe.session.user
	if user == "Guest":
		return False
	if frappe.db.get_value("User", user, "user_type") != "Website User":
		return None
	if doc.get("estado") not in VISIBLE_STATES:
		return False
	if not frappe.db.exists(
		"Usuario Aprobacion",
		{"parent": doc.name, "parenttype": "PrintCard", "user": user},
	):
		return False
	return None
