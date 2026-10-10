"""Arithmetic harness using the unchanged ERPNext/Frappe v15.122.0 source.

Only persistence, master-data lookups and HTML rendering are substituted.
This is not a replacement for the bench integration/GL tests.
Set IGC_UPSTREAM_SOURCE to a directory containing taxpy122.py/datapy122.py.
"""

import ast
import decimal
import json
import math
import os
import sys
import types
from pathlib import Path


class AttrDict(dict):
	__getattr__ = dict.get
	__setattr__ = dict.__setitem__


def setup():
	root = Path(os.environ["IGC_UPSTREAM_SOURCE"])
	frappe = types.ModuleType("frappe")
	frappe.local = types.SimpleNamespace(
		system_settings=AttrDict(rounding_method="Banker's Rounding", currency_precision="4")
	)
	frappe.flags = AttrDict()
	frappe._dict = AttrDict
	frappe._ = lambda s: s
	frappe.scrub = lambda s: s.lower().replace(" ", "_")
	frappe.get_system_settings = lambda k: frappe.local.system_settings.get(k)
	frappe.as_json = json.dumps
	frappe.throw = lambda message, **kwargs: (_ for _ in ()).throw(ValueError(message))
	frappe.db = AttrDict(get_single_value=lambda *a: 0)
	frappe.get_cached_value = lambda *a: "TST"
	frappe.get_meta = lambda dt: Meta(dt)
	frappe.company_currency = "TST"
	frappe.currencies = {}
	frappe.suppliers = {}
	frappe.tax_maps = {}
	frappe.get_cached_doc = lambda dt, name: (frappe.currencies if dt == "Currency" else frappe.suppliers)[
		name
	]
	frappe.utils = types.ModuleType("frappe.utils")
	frappe.utils.cint = lambda x: int(x or 0)
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = frappe.utils

	names = {"flt", "rounded", "_bankers_rounding", "_bankers_rounding_legacy", "_round_away_from_zero"}
	parsed = ast.parse((root / "datapy122.py").read_text())
	functions = []
	for node in parsed.body:
		if isinstance(node, ast.FunctionDef) and node.name in names and len(node.body) > 1:
			node.returns = None
			node.decorator_list = []
			for arg in node.args.args:
				arg.annotation = None
			functions.append(node)
	namespace = {
		"frappe": frappe,
		"math": math,
		"cint": frappe.utils.cint,
		"Decimal": decimal.Decimal,
		"localcontext": decimal.localcontext,
		"MAX_PREC": decimal.MAX_PREC,
		"ROUND_HALF_UP": decimal.ROUND_HALF_UP,
	}
	exec(compile(ast.Module(body=functions, type_ignores=[]), "frappe-v15.122.0-rounding", "exec"), namespace)
	frappe.utils.flt = namespace["flt"]
	Doc.frappe = frappe
	Doc.flt = staticmethod(namespace["flt"])
	parsed = ast.parse((root / "taxpy122.py").read_text())
	node = next(
		n for n in parsed.body if isinstance(n, ast.ClassDef) and n.name == "calculate_taxes_and_totals"
	)
	namespace.update(
		{
			"Document": Doc,
			"_": frappe._,
			"scrub": frappe.scrub,
			"json": json,
			"deprecated": lambda f: f,
			"erpnext": AttrDict(get_company_currency=lambda *a: frappe.company_currency),
			"get_round_off_applicable_accounts": lambda *a: None,
			"validate_conversion_rate": lambda *a: None,
			"validate_taxes_and_charges": lambda *a: None,
			"validate_inclusive_tax": lambda *a: None,
			"get_item_tax_map": lambda **kw: json.dumps(frappe.tax_maps.get(kw.get("item_tax_template"), {})),
			"get_applied_pricing_rules": lambda *a: [],
			"NOT_APPLICABLE_TAX": "N/A",
		}
	)
	exec(compile(ast.Module(body=[node], type_ignores=[]), "erpnext-v15.122.0-calculator", "exec"), namespace)
	Native = namespace["calculate_taxes_and_totals"]
	Native.set_item_wise_tax_breakup = lambda self: None  # HTML only.
	Doc.native = Native
	accounts = types.ModuleType("erpnext.controllers.accounts_controller")
	accounts.AccountsController = Doc
	taxes = types.ModuleType("erpnext.controllers.taxes_and_totals")
	taxes.calculate_taxes_and_totals = Native
	for name in ("erpnext", "erpnext.controllers"):
		sys.modules[name] = types.ModuleType(name)
	sys.modules[accounts.__name__] = accounts
	sys.modules[taxes.__name__] = taxes
	return frappe, Native


MONEY = set(
	"""rate net_rate price_list_rate rate_with_margin discount_amount margin_rate_or_amount amount net_amount
distributed_discount_amount item_tax_amount total net_total grand_total total_taxes_and_charges taxes_and_charges_added
taxes_and_charges_deducted tax_amount tax_amount_after_discount_amount rounded_total rounding_adjustment total_advance
paid_amount write_off_amount outstanding_amount valuation_rate incoming_rate sales_incoming_rate""".split()
)
MONEY |= {"base_" + f for f in tuple(MONEY)}
FLOATS = {
	"qty": 4,
	"conversion_factor": 9,
	"conversion_rate": 9,
	"discount_percentage": 2,
	"additional_discount_percentage": 2,
}


class Meta:
	def __init__(self, doctype):
		self.doctype = doctype
		self.fields = [AttrDict(fieldname=n, fieldtype="Currency", options=None) for n in MONEY]
		self.fields += [AttrDict(fieldname=n, fieldtype="Float", options=None) for n in FLOATS]
		if doctype == "Purchase Taxes and Charges":
			self.fields = [f for f in self.fields if f.fieldname != "rate"]
			self.fields.append(AttrDict(fieldname="rate", fieldtype="Percent", options=None))

	def get_field(self, name):
		if name in ("items", "taxes"):
			return AttrDict(
				options=self.doctype + " Item" if name == "items" else "Purchase Taxes and Charges"
			)
		return next((f for f in self.fields if f.fieldname == name), None)

	def get(self, name, filters=None):
		return self.fields

	def get_label(self, name):
		return name


class Status(int):
	def is_cancelled(self):
		return self == 2


class Doc:
	def __init__(self, doctype, **values):
		self.doctype = doctype
		self.meta = Meta(doctype)
		for field in self.meta.fields:
			setattr(self, field.fieldname, 0)
		self.docstatus = Status(0)
		self.disable_rounded_total = 1
		self.items = []
		self.taxes = []
		self.advances = []
		self.apply_discount_on = "Net Total"
		self.__dict__.update(values)

	def __getattr__(self, name):
		return None

	def get(self, name, default=None):
		return self.__dict__.get(name, default)

	def set(self, name, value):
		setattr(self, name, value)

	def get_all_children(self):
		return self.items + self.taxes + self.advances

	def get_doc_before_save(self):
		return self.get("previous")

	def precision(self, name, parentfield=None):
		if parentfield and not isinstance(parentfield, str):
			parentfield = parentfield.parentfield
		meta = (
			self.meta
			if not parentfield
			else Meta(self.doctype + " Item" if parentfield == "items" else "Purchase Taxes and Charges")
		)
		field = meta.get_field(name)
		if not field:
			return None
		return 4 if field.fieldtype == "Currency" else FLOATS.get(name, 2)

	def round_floats_in(self, doc, fieldnames=None, do_not_round_fields=None):
		for name in fieldnames or [f.fieldname for f in doc.meta.fields]:
			if do_not_round_fields and name in do_not_round_fields:
				continue
			doc.set(name, self.flt(doc.get(name), self.precision(name, doc.get("parentfield"))))

	def calculate_taxes_and_totals(self):
		self.native(self)

	def is_rounded_total_disabled(self):
		return self.disable_rounded_total

	def is_internal_transfer(self):
		return False


def document(doctype="Purchase Order", rates=(394.0678,), quantities=(3,), currency="TST", tax_rate=18):
	doc = Doc(
		doctype,
		company="Test Company",
		currency=currency,
		conversion_rate=1,
		supplier="Test Supplier",
		party_account_currency=currency,
	)
	for idx, (rate, qty) in enumerate(zip(rates, quantities, strict=True), 1):
		doc.items.append(
			Doc(
				doctype + " Item",
				idx=idx,
				name=f"row-{idx}",
				parentfield="items",
				item_code=f"item-{idx}",
				rate=rate,
				qty=qty,
			)
		)
	if tax_rate is not None:
		doc.taxes.append(
			Doc(
				"Purchase Taxes and Charges",
				idx=1,
				name="tax-1",
				parentfield="taxes",
				charge_type="On Net Total",
				account_head="Tax",
				rate=tax_rate,
				category="Total",
				add_deduct_tax="Add",
			)
		)
	return doc
