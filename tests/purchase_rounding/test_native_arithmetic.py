import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from native_harness import AttrDict, Doc, Status, document, setup

frappe, Native = setup()
from igctools.purchase_rounding.engine import bind, calculate, policy_scope
from igctools.purchase_rounding.policy import PREFIX, Policy, resolve, validate_currency


class TestNativeArithmetic(unittest.TestCase):
	def setUp(self):
		frappe.flags.clear()
		frappe.local.system_settings = AttrDict(rounding_method="Banker's Rounding", currency_precision="4")
		frappe.currencies = {
			"TST": AttrDict(
				{
					PREFIX + k: v
					for k, v in {
						"rounding_enabled": 1,
						"rate_precision": "4",
						"amount_precision": "2",
						"tax_precision": "2",
						"total_precision": "2",
						"rounding_method": "Commercial Rounding",
						"tax_rounding": "Por subtotal",
					}.items()
				}
			),
			"USD": AttrDict(),
		}
		frappe.suppliers = {"Test Supplier": AttrDict()}
		frappe.tax_maps = {}
		frappe.company_currency = "TST"

	def test_vendor_arithmetic_all_purchase_types(self):
		for doctype in ("Supplier Quotation", "Purchase Order", "Purchase Receipt", "Purchase Invoice"):
			with self.subTest(doctype=doctype):
				doc = document(doctype)
				bind(doc)
				doc.calculate_taxes_and_totals()
				self.assertEqual(
					(
						doc.items[0].rate,
						doc.items[0].amount,
						doc.net_total,
						doc.taxes[0].tax_amount,
						doc.grand_total,
					),
					(394.0678, 1182.2, 1182.2, 212.8, 1395.0),
				)
				self.assertEqual(doc.discount_amount, 0)
				self.assertEqual(doc.taxes[0].charge_type, "On Net Total")

	def test_disabled_currency_matches_native_exactly(self):
		for currency in ("USD", "TST"):
			frappe.currencies[currency][PREFIX + "rounding_enabled"] = 0
			native = document(currency=currency)
			doc = document(currency=currency)
			Native(native)
			calculate(doc)
			self.assertEqual(
				(doc.total, doc.grand_total, doc.taxes[0].tax_amount, doc.items[0].rate),
				(native.total, native.grand_total, native.taxes[0].tax_amount, native.items[0].rate),
			)
			self.assertEqual(doc.total, 1182.2034)

	def test_commercial_ties_and_returns(self):
		for rate, expected in ((2.345, 2.35), (2.3449, 2.34), (2.355, 2.36), (0, 0)):
			for sign in (1, -1):
				doc = document(rates=(rate,), quantities=(sign,), tax_rate=None)
				doc.is_return = sign < 0
				calculate(doc)
				self.assertEqual(doc.total, sign * expected)

	def test_supplier_tax_basis_choice(self):
		for mode, expected in (("Por subtotal", 0.01), ("Por linea", 0.02)):
			frappe.suppliers["Test Supplier"][PREFIX + "tax_rounding"] = mode
			doc = document(rates=(0.05, 0.05), quantities=(1, 1), tax_rate=10)
			calculate(doc)
			self.assertEqual(doc.taxes[0].tax_amount, expected)

	def test_inclusive_tax(self):
		doc = document(rates=(465,), quantities=(3,))
		doc.taxes[0].included_in_print_rate = 1
		calculate(doc)
		self.assertEqual(
			(doc.total, doc.net_total, doc.taxes[0].tax_amount, doc.grand_total), (1395, 1182.2, 212.8, 1395)
		)

	def test_actual_tax_survives_recalculation_and_exchange_rate(self):
		doc = document(currency="USD")
		doc.taxes[0].charge_type = "Actual"
		doc.taxes[0].tax_amount = 212.8
		for rate in (60, 61.75, 62):
			doc.conversion_rate = rate
			calculate(doc)
			self.assertEqual(doc.taxes[0].tax_amount, 212.8)
			self.assertEqual(doc.items[0].rate, 394.0678)

	def test_discount_net_total(self):
		doc = document()
		doc.discount_amount = 100
		calculate(doc)
		self.assertEqual((doc.net_total, doc.taxes[0].tax_amount, doc.grand_total), (1082.2, 194.8, 1277))

	def test_discount_grand_total(self):
		doc = document()
		doc.apply_discount_on = "Grand Total"
		doc.discount_amount = 118
		calculate(doc)
		self.assertEqual(
			(doc.net_total, doc.taxes[0].tax_amount_after_discount_amount, doc.grand_total),
			(1082.2, 194.8, 1277),
		)
		self.assertEqual(doc.taxes[0].tax_amount, 212.8)

	def test_percentage_discount(self):
		doc = document(rates=(100.05,), quantities=(1,))
		doc.additional_discount_percentage = 10
		calculate(doc)
		self.assertEqual(
			(doc.discount_amount, doc.net_total, doc.taxes[0].tax_amount, doc.grand_total),
			(10.01, 90.04, 16.21, 106.25),
		)

	def test_exempt_and_taxed_lines(self):
		doc = document(rates=(100, 100), quantities=(1, 1))
		frappe.tax_maps["Exempt"] = {"Tax": 0}
		doc.items[1].item_tax_template = "Exempt"
		# Only template eligibility validation needs Item master data; the native
		# map, mixed-rate and tax arithmetic are exercised without replacement.
		original = Native.validate_item_tax_template
		Native.validate_item_tax_template = lambda self: None
		try:
			calculate(doc)
		finally:
			Native.validate_item_tax_template = original
		self.assertEqual((doc.net_total, doc.taxes[0].tax_amount, doc.grand_total), (200, 18, 218))

	def test_nested_tax_and_deduction(self):
		doc = document(rates=(100,), quantities=(1,))
		doc.taxes.append(
			Doc(
				"Purchase Taxes and Charges",
				idx=2,
				parentfield="taxes",
				charge_type="On Previous Row Amount",
				row_id=1,
				rate=10,
				account_head="Retention",
				add_deduct_tax="Deduct",
				category="Total",
			)
		)
		calculate(doc)
		self.assertEqual(
			(doc.taxes[0].tax_amount, doc.taxes[1].tax_amount, doc.grand_total), (18, 1.8, 116.2)
		)

	def test_repeated_calculation_is_idempotent(self):
		doc = document(rates=(12.3456, 10.005), quantities=(3, 7))
		doc.discount_amount = 3.11
		calculate(doc)
		first = (doc.total, doc.net_total, doc.taxes[0].tax_amount, doc.grand_total)
		for _ in range(5):
			calculate(doc)
			self.assertEqual((doc.total, doc.net_total, doc.taxes[0].tax_amount, doc.grand_total), first)

	def test_scope_restores_settings_flags_and_methods_after_error(self):
		doc = document()
		settings = frappe.local.system_settings
		frappe.flags.round_row_wise_tax = "sentinel"
		frappe.flags.round_off_applicable_accounts = ["original"]
		with self.assertRaisesRegex(RuntimeError, "intentional"):
			with policy_scope(doc, resolve(doc)):
				self.assertEqual(doc.precision("amount", "items"), 2)
				self.assertEqual(doc.items[0].precision("rate"), 4)
				raise RuntimeError("intentional")
		self.assertIs(frappe.local.system_settings, settings)
		self.assertEqual(frappe.flags.round_row_wise_tax, "sentinel")
		self.assertEqual(frappe.flags.round_off_applicable_accounts, ["original"])
		for row in [doc, *doc.get_all_children()]:
			self.assertNotIn("precision", row.__dict__)
		self.assertNotIn("round_floats_in", doc.__dict__)

	def test_quantities_and_fx_keep_native_precision(self):
		doc = document(quantities=(1.23445,))
		calculate(doc)
		self.assertEqual(doc.items[0].qty, 1.2344)
		self.assertEqual(doc.items[0].precision("qty"), 4)
		self.assertEqual(doc.precision("conversion_rate"), 9)

	def test_submitted_documents_do_not_resolve_policy(self):
		doc = document()
		doc.docstatus = Status(1)
		self.assertIsNone(resolve(doc))
		doc.docstatus = Status(0)
		doc.previous = AttrDict(docstatus=1)
		self.assertIsNone(resolve(doc))

	def test_submit_and_opt_out(self):
		doc = document()
		doc.docstatus = Status(1)
		doc._action = "submit"
		self.assertIsNotNone(resolve(doc))
		doc.custom_igc_use_native_rounding = 1
		self.assertIsNone(resolve(doc))

	def test_configurable_precision_and_invalid_configuration(self):
		settings = frappe.currencies["TST"]
		for part in ("amount", "tax", "total"):
			settings[PREFIX + part + "_precision"] = "3"
		doc = document()
		calculate(doc)
		self.assertEqual(doc.items[0].amount, 1182.203)
		settings[PREFIX + "total_precision"] = "2"
		with self.assertRaises(ValueError):
			validate_currency(settings)


if __name__ == "__main__":
	unittest.main()
