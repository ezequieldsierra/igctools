"""Run on a disposable ERPNext test site, never against production data."""

import unittest

import frappe
from frappe.tests.utils import FrappeTestCase, change_settings

from igctools.purchase_rounding.api import preview
from igctools.purchase_rounding.policy import PREFIX
from igctools.purchase_rounding.setup import install


class TestPurchaseRoundingIntegration(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		if "erpnext" not in frappe.get_installed_apps():
			raise unittest.SkipTest("Requires ERPNext; IGCTools also supports Frappe-only sites.")
		super().setUpClass()
		from frappe.test_runner import make_test_records

		install()
		for doctype in ("Purchase Invoice", "Purchase Order", "Purchase Receipt", "Supplier Quotation"):
			make_test_records(doctype)
		cls.tax_account = frappe.db.get_value(
			"Account", {"company": "_Test Company", "account_type": "Tax", "is_group": 0}, "name"
		)
		if not cls.tax_account:
			parent = frappe.db.get_value(
				"Account", {"company": "_Test Company", "is_group": 1, "root_type": "Asset"}, "name"
			)
			cls.tax_account = (
				frappe.get_doc(
					{
						"doctype": "Account",
						"company": "_Test Company",
						"account_name": "_Test Rounding Tax",
						"account_type": "Tax",
						"parent_account": parent,
						"is_group": 0,
					}
				)
				.insert()
				.name
			)
		frappe.db.commit()  # Commit test fixtures only; each test rolls back.

	def setUp(self):
		self.settings = change_settings(
			"System Settings", {"currency_precision": "4", "rounding_method": "Banker's Rounding"}
		)
		self.settings.__enter__()
		self.addCleanup(self.settings.__exit__, None, None, None)
		self.currency = frappe.get_doc("Currency", "INR")
		self.currency.update(
			{
				PREFIX + key: value
				for key, value in {
					"rounding_enabled": 1,
					"rate_precision": "4",
					"amount_precision": "2",
					"tax_precision": "2",
					"total_precision": "2",
					"rounding_method": "Commercial Rounding",
					"tax_rounding": "Por subtotal",
				}.items()
			}
		)
		self.currency.save()

	def tearDown(self):
		frappe.db.rollback()
		frappe.clear_document_cache("Currency", "INR")
		frappe.clear_document_cache("Supplier", "_Test Supplier")

	def order(self):
		from erpnext.buying.doctype.purchase_order.test_purchase_order import create_purchase_order

		doc = create_purchase_order(do_not_save=True, qty=3, rate=394.0678)
		doc.disable_rounded_total = 1
		doc.append(
			"taxes",
			{
				"charge_type": "On Net Total",
				"rate": 18,
				"account_head": self.tax_account,
				"description": "Tax",
				"category": "Total",
				"add_deduct_tax": "Add",
			},
		)
		return doc

	def assert_amounts(self, doc):
		self.assertEqual(doc.items[0].rate, 394.0678)
		self.assertEqual(doc.items[0].amount, 1182.2)
		self.assertEqual(doc.net_total, 1182.2)
		self.assertEqual(doc.taxes[0].tax_amount, 212.8)
		self.assertEqual(doc.grand_total, 1395)
		self.assertEqual(doc.discount_amount, 0)

	def test_po_receipt_invoice_and_balanced_ledger(self):
		from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
		from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice

		po = self.order().insert()
		self.assert_amounts(po)
		po.submit()
		pr = make_purchase_receipt(po.name)
		pr.disable_rounded_total = 1
		pr.insert()
		self.assert_amounts(pr)
		pr.submit()
		pi = make_purchase_invoice(pr.name)
		pi.disable_rounded_total = 1
		pi.insert()
		self.assert_amounts(pi)
		pi.submit()
		pi.reload()
		self.assert_amounts(pi)
		ledger = frappe.get_all(
			"GL Entry",
			filters={"voucher_type": pi.doctype, "voucher_no": pi.name, "is_cancelled": 0},
			fields=["debit", "credit"],
		)
		self.assertTrue(ledger)
		self.assertAlmostEqual(sum(row.debit - row.credit for row in ledger), 0, places=4)
		self.assertEqual(frappe.db.get_single_value("System Settings", "currency_precision"), "4")
		self.assertEqual(frappe.get_system_settings("rounding_method"), "Banker's Rounding")

	def test_supplier_quotation_save(self):
		po = self.order()
		sq = frappe.new_doc("Supplier Quotation")
		for field in ("company", "supplier", "currency", "conversion_rate", "transaction_date"):
			sq.set(field, po.get(field))
		sq.disable_rounded_total = 1
		for row in po.items:
			sq.append(
				"items",
				{"item_code": row.item_code, "qty": row.qty, "rate": row.rate, "warehouse": row.warehouse},
			)
		for row in po.taxes:
			sq.append(
				"taxes",
				{
					field: row.get(field)
					for field in (
						"charge_type",
						"rate",
						"account_head",
						"description",
						"category",
						"add_deduct_tax",
					)
				},
			)
		sq.insert()
		self.assert_amounts(sq)

	def test_preview_matches_save_without_writing(self):
		doc = self.order()
		doc.set_missing_values()
		count_before = frappe.db.count("Purchase Order")
		result = preview(frappe.as_json(doc.as_dict()))
		self.assertTrue(result["enabled"])
		self.assertEqual(frappe.db.count("Purchase Order"), count_before)
		doc.insert()
		self.assertEqual(doc.grand_total, result["values"]["grand_total"])
		self.assertEqual(doc.net_total, result["values"]["net_total"])

	def test_submitted_policy_snapshot_survives_configuration_changes(self):
		doc = self.order().insert().submit()
		before = (doc.modified, doc.total, doc.grand_total, doc.custom_igc_rounding_policy)
		self.currency.set(PREFIX + "rounding_enabled", 0)
		self.currency.save()
		result = preview(frappe.as_json(doc.as_dict()))
		self.assertTrue(result["enabled"])
		self.assertFalse(result["editable"])
		doc.reload()
		self.assertEqual((doc.modified, doc.total, doc.grand_total, doc.custom_igc_rounding_policy), before)

	def test_native_opt_out_keeps_four_decimals(self):
		doc = self.order()
		doc.custom_igc_use_native_rounding = 1
		doc.insert()
		self.assertEqual(doc.total, 1182.2034)
		self.assertEqual(doc.taxes[0].tax_amount, 212.7966)
		self.assertFalse(doc.custom_igc_rounding_policy)

	def test_actual_tax_and_input_type_preserved(self):
		doc = self.order()
		doc.taxes[0].charge_type = "Actual"
		doc.taxes[0].tax_amount = 212.8
		doc.taxes[0].rate = 0
		doc.insert()
		self.assert_amounts(doc)
		self.assertEqual(doc.taxes[0].charge_type, "Actual")

	def test_invalid_precision_is_rejected(self):
		self.currency.set(PREFIX + "amount_precision", "3")
		with self.assertRaises(frappe.ValidationError):
			self.currency.save()
