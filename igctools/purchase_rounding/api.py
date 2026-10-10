"""Read-only form preview; saving always recalculates on the server."""

import frappe
from frappe import _
from frappe.utils import cint

from igctools.purchase_rounding.engine import calculate
from igctools.purchase_rounding.policy import PURCHASE_DOCTYPES, Policy, resolve


@frappe.whitelist()
def preview(document):
	payload = frappe.parse_json(document)
	if not isinstance(payload, dict) or payload.get("doctype") not in PURCHASE_DOCTYPES:
		frappe.throw(_("Invalid purchase document."))
	doc = frappe.get_doc(payload)
	policy = None
	editable = not cint(payload.get("docstatus"))
	if doc.get("name") and not doc.get("__islocal") and frappe.db.exists(doc.doctype, doc.name):
		saved = frappe.get_doc(doc.doctype, doc.name)
		if cint(saved.docstatus):
			saved.check_permission("read")
			doc = saved
			editable = False
			snapshot = frappe.parse_json(saved.get("custom_igc_rounding_policy") or "null")
			if snapshot:
				policy = Policy(**snapshot)
		else:
			saved.check_permission("write")
	else:
		if not editable:
			return {"enabled": False}
		doc.check_permission("create")
	if editable:
		policy = resolve(doc)
	if not policy:
		return {"enabled": False}
	# An incomplete form needs precision metadata, but no financial calculation.
	if editable and doc.get("items"):
		calculate(doc)

	def values(current):
		return {
			field.fieldname: current.get(field.fieldname)
			for field in current.meta.fields
			if field.fieldtype == "Currency"
		}

	def precision(current):
		return {
			field.fieldname: value
			for field in current.meta.fields
			if (value := policy.precision_for(current, field.fieldname, field)) is not None
		}

	result = {
		"enabled": True,
		"editable": editable,
		"policy": policy.as_dict(),
		"values": values(doc),
		"precision": precision(doc),
		"tables": {},
	}
	for table in ("items", "taxes", "payment_schedule"):
		result["tables"][table] = [
			{
				"name": row.name,
				"values": values(row),
				"precision": precision(row),
				"item_wise_tax_detail": row.get("item_wise_tax_detail"),
			}
			for row in doc.get(table) or []
		]
	if doc.meta.get_field("other_charges_calculation"):
		result["other_charges_calculation"] = doc.other_charges_calculation
	return result
