"""Resolve editable policies without changing site-wide monetary settings."""

from dataclasses import asdict, dataclass

import frappe
from frappe import _
from frappe.utils import cint

PURCHASE_DOCTYPES = ("Supplier Quotation", "Purchase Order", "Purchase Receipt", "Purchase Invoice")
PREFIX = "custom_igc_purchase_"
METHODS = ("Sistema", "Commercial Rounding", "Banker's Rounding", "Banker's Rounding (legacy)")
TAX_MODES = ("Sistema", "Por subtotal", "Por linea")
UNIT_FIELDS = frozenset(
	{
		"rate",
		"net_rate",
		"price_list_rate",
		"rate_with_margin",
		"valuation_rate",
		"incoming_rate",
		"sales_incoming_rate",
		"stock_uom_rate",
		"discount_amount",
		"margin_rate_or_amount",
	}
)


@dataclass(frozen=True)
class Policy:
	currency: str
	rate_precision: int
	amount_precision: int
	tax_precision: int
	total_precision: int
	rounding_method: str
	tax_rounding: str
	company_currency: str

	def as_dict(self):
		return asdict(self)

	def precision_for(self, document, fieldname, field):
		if not field or field.fieldtype != "Currency":
			return None
		# Foreign-currency ledger values retain their native precision. The policy
		# describes the currency of this transaction, not its exchange-rate inputs.
		if fieldname.startswith("base_") and self.currency != self.company_currency:
			return None
		if "price_list_currency" in (field.options or "") and document.get("price_list_currency") not in (
			None,
			"",
			self.currency,
		):
			return None
		name = fieldname.removeprefix("base_")
		if document.doctype.endswith(" Item"):
			return self.rate_precision if name in UNIT_FIELDS else self.amount_precision
		if document.doctype == "Purchase Taxes and Charges":
			return self.total_precision if name == "total" else self.tax_precision
		return self.total_precision


def validate_currency(doc, method=None):
	if not cint(doc.get(PREFIX + "rounding_enabled")):
		return
	values = []
	for part in ("rate", "amount", "tax", "total"):
		value = str(doc.get(PREFIX + part + "_precision") or "")
		if value not in tuple(str(n) for n in range(10)):
			frappe.throw(_("Purchase rounding precision must be an integer between 0 and 9."))
		values.append(int(value))
	if values[3] < max(values[1], values[2]):
		frappe.throw(_("Total precision must be at least the item amount and tax precision."))
	if doc.get(PREFIX + "rounding_method") not in METHODS:
		frappe.throw(_("Choose a valid purchase rounding method."))
	if doc.get(PREFIX + "tax_rounding") not in TAX_MODES:
		frappe.throw(_("Choose a valid purchase tax rounding mode."))


def resolve(doc):
	if doc.doctype not in PURCHASE_DOCTYPES or doc.get("custom_igc_use_native_rounding"):
		return None
	if not doc.get("currency") or not doc.get("company"):
		return None
	if cint(doc.docstatus) and getattr(doc, "_action", None) != "submit":
		return None
	# An update to an already submitted document must never reprice it.
	previous = doc.get_doc_before_save()
	if previous and cint(previous.docstatus):
		return None
	currency = frappe.get_cached_doc("Currency", doc.currency)
	if not cint(currency.get(PREFIX + "rounding_enabled")):
		return None
	validate_currency(currency)
	tax_mode = currency.get(PREFIX + "tax_rounding")
	if doc.get("supplier"):
		override = frappe.get_cached_doc("Supplier", doc.supplier).get(PREFIX + "tax_rounding")
		if override in TAX_MODES:
			tax_mode = override
	return Policy(
		currency=doc.currency,
		rate_precision=int(currency.get(PREFIX + "rate_precision")),
		amount_precision=int(currency.get(PREFIX + "amount_precision")),
		tax_precision=int(currency.get(PREFIX + "tax_precision")),
		total_precision=int(currency.get(PREFIX + "total_precision")),
		rounding_method=currency.get(PREFIX + "rounding_method"),
		tax_rounding=tax_mode,
		company_currency=frappe.get_cached_value("Company", doc.company, "default_currency"),
	)
