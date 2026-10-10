"""Reuse ERPNext's calculator with an isolated, exception-safe policy scope.

No module functions, shared metadata, DB settings or controller classes are
patched. All temporary attributes belong to the current document/request and
are restored in finally, including when native validation raises an exception.
"""

from contextlib import contextmanager
from functools import wraps

import frappe
from frappe import _
from frappe.utils import flt

from igctools.purchase_rounding.policy import PURCHASE_DOCTYPES, resolve

MISSING = object()


def _restore(obj, name, previous):
	if previous is MISSING:
		obj.__dict__.pop(name, None)
	else:
		setattr(obj, name, previous)


def _field(document, name, parentfield=None):
	if parentfield and not isinstance(parentfield, str):
		parentfield = parentfield.parentfield
	if parentfield:
		child_type = document.meta.get_field(parentfield).options
		return frappe.get_meta(child_type).get_field(name), child_type
	return document.meta.get_field(name), document.doctype


@contextmanager
def policy_scope(doc, policy):
	# Copy, never mutate the cached System Settings document. frappe.local is
	# request-local. Nested scopes and exceptions restore the original object.
	frappe.get_system_settings("rounding_method")
	previous_settings = frappe.local.system_settings
	native_method = previous_settings.get("rounding_method") or "Banker's Rounding (legacy)"
	settings = frappe._dict(
		previous_settings if isinstance(previous_settings, dict) else previous_settings.as_dict()
	)
	if policy.rounding_method != "Sistema":
		settings.rounding_method = policy.rounding_method
	previous_flags = {
		key: frappe.flags.get(key, MISSING) for key in ("round_row_wise_tax", "round_off_applicable_accounts")
	}
	previous_round = doc.__dict__.get("round_floats_in", MISSING)
	precision_methods = []

	def install_precision(current):
		original = current.precision
		precision_methods.append((current, current.__dict__.get("precision", MISSING)))

		def precision(name, parentfield=None):
			field, doctype = _field(current, name, parentfield)
			# Resolve child fields using a lightweight description, never shared meta.
			target = current if doctype == current.doctype else frappe._dict(doctype=doctype)
			value = policy.precision_for(target, name, field)
			return original(name, parentfield) if value is None else value

		current.precision = precision

	def round_floats_in(current, fieldnames=None, do_not_round_fields=None):
		fields = fieldnames or [
			field.fieldname
			for field in current.meta.fields
			if field.fieldtype in ("Currency", "Float", "Percent")
		]
		for name in fields:
			if do_not_round_fields and name in do_not_round_fields:
				continue
			field = current.meta.get_field(name)
			method = settings.rounding_method if field and field.fieldtype == "Currency" else native_method
			current.set(
				name,
				flt(
					current.get(name), doc.precision(name, current.get("parentfield")), rounding_method=method
				),
			)

	try:
		frappe.local.system_settings = settings
		for current in [doc, *doc.get_all_children()]:
			install_precision(current)
		doc.round_floats_in = round_floats_in
		yield
	finally:
		_restore(doc, "round_floats_in", previous_round)
		for current, previous in reversed(precision_methods):
			_restore(current, "precision", previous)
		frappe.local.system_settings = previous_settings
		for key, previous in previous_flags.items():
			if previous is MISSING:
				frappe.flags.pop(key, None)
			else:
				frappe.flags[key] = previous


def calculate(doc, original=None):
	policy = resolve(doc)
	original = original or doc.calculate_taxes_and_totals
	if policy is None:
		result = original()
		if not doc.docstatus:
			doc.custom_igc_rounding_policy = None
		return result
	# Imports are lazy so IGCTools remains installable on Frappe-only sites.
	from erpnext.controllers.accounts_controller import AccountsController
	from erpnext.controllers.taxes_and_totals import calculate_taxes_and_totals as NativeCalculator

	if getattr(original, "__func__", None) is not AccountsController.calculate_taxes_and_totals:
		frappe.throw(
			_(
				"Purchase rounding requires review: another extension replaces this document's tax calculator. Disable the currency policy to use that calculator."
			)
		)

	class PolicyCalculator(NativeCalculator):
		def calculate(self, *args, **kwargs):
			# Native __init__ loads the global flag; select the policy afterwards.
			if policy.tax_rounding != "Sistema":
				frappe.flags.round_row_wise_tax = policy.tax_rounding == "Por linea"
			return super().calculate(*args, **kwargs)

	with policy_scope(doc, policy):
		if not doc.get("additional_discount_percentage"):
			doc.discount_amount = flt(doc.get("discount_amount"), policy.total_precision)
		PolicyCalculator(doc)
	doc.custom_igc_rounding_policy = frappe.as_json(policy.as_dict())


def bind(doc, method=None):
	"""before_validate: all normal saves, imports and submissions enter here."""
	if doc.doctype not in PURCHASE_DOCTYPES:
		return
	original = doc.calculate_taxes_and_totals
	if getattr(original, "igc_purchase_rounding", False):
		return

	@wraps(original)
	def wrapped(*args, **kwargs):
		if args or kwargs:
			return original(*args, **kwargs)
		return calculate(doc, original)

	wrapped.igc_purchase_rounding = True
	doc.calculate_taxes_and_totals = wrapped
