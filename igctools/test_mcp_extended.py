"""Real-document integration tests for print formats and console execution."""

import json
import unittest
import uuid
from unittest.mock import patch

import frappe

from igctools import mcp
from igctools import mcp_auth as auth
from igctools import mcp_console as console
from igctools import mcp_printing as printing
from igctools.test_mcp import TestScriptMCP


class TestExtendedMCP(TestScriptMCP):
	def new_format(self):
		return printing.save_print_format(
			"IGC MCP PF Test " + uuid.uuid4().hex,
			"ToDo",
			"<h1>Original</h1><p>{{ doc.description }}</p>",
			"h1 {color: navy}",
			"Integration test",
		)

	def test_format_create_read_update_restore_and_permissions(self):
		created = self.new_format()
		name = created["name"]
		old = printing.read_print_format(name)
		self.assertEqual(json.loads(old["document_json"])["doc_type"], "ToDo")
		changed = printing.save_print_format(
			name, "ToDo", "<h1>Changed</h1>", "", "Test update", created["revision"]
		)
		with self.assertRaises(frappe.TimestampMismatchError):
			printing.save_print_format(name, "ToDo", "<h1>Stale</h1>", "", "Stale test", created["revision"])
		restored = printing.restore_print_format(
			name, changed["revision"], changed["audit_id"], "Restore test"
		)
		self.assertEqual(
			frappe.get_doc("Print Format", name).html, "<h1>Original</h1><p>{{ doc.description }}</p>"
		)
		self.assertNotEqual(restored["audit_id"], changed["audit_id"])
		self.assertEqual(len(printing.print_format_history(name)["changes"]), 3)
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			printing.read_print_format(name)

	def test_invalid_format_rolls_back_and_existing_identity_is_preserved(self):
		created = self.new_format()
		before = frappe.db.count("IGC MCP Change")
		result = self.invoke(
			"save_print_format",
			{
				"name": created["name"],
				"doc_type": "ToDo",
				"html": "{% if %}",
				"css": "",
				"reason": "Invalid template",
				"expected_revision": created["revision"],
			},
		)
		self.assertTrue(result["result"]["isError"])
		self.assertEqual(frappe.db.count("IGC MCP Change"), before)
		self.assertEqual(printing.read_print_format(created["name"])["revision"], created["revision"])
		with self.assertRaises(frappe.ValidationError):
			printing.save_print_format(
				created["name"], "User", "<p>Wrong</p>", "", "Wrong DocType", created["revision"]
			)

	def test_default_is_revision_checked_and_archived(self):
		created = self.new_format()
		context = printing.get_print_context("ToDo")
		before = json.loads(printing.scripts.serialize(printing.default_state("ToDo")))
		result = printing.set_default_print_format(
			created["name"], created["revision"], context["default_revision"], "Default test"
		)
		self.addCleanup(lambda: frappe.clear_cache(doctype="ToDo"))
		self.assertEqual(result["default_print_format"], created["name"])
		audit = frappe.get_doc("IGC MCP Change", result["audit_id"])
		self.assertEqual(audit.script_type, "Print Default")
		self.assertEqual(json.loads(audit.before_snapshot), before)
		with self.assertRaises(frappe.TimestampMismatchError):
			printing.set_default_print_format(
				created["name"], created["revision"], context["default_revision"], "Stale default"
			)

	def test_print_preview_renders_document_content(self):
		created = self.new_format()
		doc = frappe.get_doc({"doctype": "ToDo", "description": "UNIQUE PRINT PREVIEW VALUE"}).insert()
		result = printing.preview_print_format(created["name"], doc.name)
		self.assertIn("UNIQUE PRINT PREVIEW VALUE", result["content"])

	def test_console_schema_annotation_idempotency_and_output_pagination(self):
		definition = next(t for t in mcp.TOOLS if t["name"] == "execute_system_console")
		self.assertFalse(definition["annotations"]["readOnlyHint"])
		self.assertTrue(definition["annotations"]["openWorldHint"])
		args = {"script": "print('OK')", "request_id": uuid.uuid4().hex, "reason": "Test", "commit": False}
		with patch.object(frappe, "enqueue") as enqueue:
			first = console.execute_system_console(**args)
			second = console.execute_system_console(**args)
			self.assertEqual(first["execution_id"], second["execution_id"])
			self.assertTrue(enqueue.call_args.kwargs["deduplicate"])
			self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])
			with self.assertRaises(frappe.ValidationError):
				console.execute_system_console(**{**args, "script": "print('DIFFERENT')"})
		mcp.validate_arguments(definition, args)
		with self.assertRaises(ValueError):
			mcp.validate_arguments(definition, {**args, "commit": 1})
		frappe.db.set_value(
			console.RUN_TYPE, first["execution_id"], {"status": "Completed", "output": "abcdef"}
		)
		out = console.read_console_output(first["execution_id"], offset=1, length=3)
		self.assertEqual(out["content"], "bcd")
		self.assertEqual(out["next_offset"], 4)

	def test_console_native_restrictions_output_errors_and_truncation(self):
		from frappe.utils import safe_exec

		if not safe_exec.is_safe_exec_enabled():
			self.skipTest("Native Server Scripts are disabled; do not enable them during tests")
		result = console.run_source("print('Before error')\nvalue = 1 / 0", "Python")
		self.assertEqual(result["output"], "Before error")
		self.assertEqual(result["error_type"], "ZeroDivisionError")
		self.assertIn("Traceback", result["error"])
		self.assertTrue(console.run_source("import os", "Python")["error_type"])
		self.assertTrue(console.run_source("frappe.db.commit()", "Python")["error_type"])
		self.assertTrue(console.run_source("DELETE FROM `tabToDo`", "SQL")["error_type"])
		with patch.object(console, "MAX_OUTPUT", 8):
			large = console.run_source("print('abcdefghijklmn')", "Python")
			self.assertTrue(large["output_truncated"])
			self.assertLessEqual(len(large["output"]), 8)

	def test_console_permission_checked_before_queue(self):
		frappe.set_user("Guest")
		with patch.object(frappe, "enqueue") as enqueue:
			with self.assertRaises(frappe.PermissionError):
				console.execute_system_console("print('x')", uuid.uuid4().hex, "Test")
			enqueue.assert_not_called()


class TestConsoleTransactions(unittest.TestCase):
	def setUp(self):
		from frappe.utils.safe_exec import is_safe_exec_enabled

		if not is_safe_exec_enabled():
			self.skipTest("Native Server Scripts are disabled")
		frappe.set_user("Administrator")
		self.settings = frappe._dict(enabled=1, allowed_user="Administrator")
		self.settings_patch = patch.object(auth, "get_settings", return_value=self.settings)
		self.settings_patch.start()
		self.addCleanup(self.settings_patch.stop)
		self.sentinel = frappe.get_doc(
			{"doctype": "ToDo", "description": "MCP test baseline " + uuid.uuid4().hex}
		).insert()
		self.baseline = self.sentinel.description
		frappe.db.commit()
		self.execution_id = None
		self.source = None

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		if self.execution_id:
			frappe.db.delete(console.RUN_TYPE, {"name": self.execution_id})
		if self.source:
			frappe.db.delete("Console Log", {"script": self.source})
		frappe.db.delete("ToDo", {"name": self.sentinel.name})
		frappe.db.commit()

	def queue(self, commit=False, fail=False):
		self.source = (
			"frappe.db.set_value('ToDo', "
			+ repr(self.sentinel.name)
			+ ", 'description', 'MCP test changed')\nprint('execution output')"
		)
		if fail:
			self.source += "\nvalue = 1 / 0"
		with patch.object(frappe, "enqueue"):
			queued = console.execute_system_console(
				self.source, uuid.uuid4().hex, "Transaction integration test", commit=commit
			)
		self.execution_id = queued["execution_id"]
		frappe.db.commit()

	def test_no_commit_rolls_back_changes_but_retains_output(self):
		self.queue()
		console.run_execution(self.execution_id)
		self.assertEqual(frappe.db.get_value("ToDo", self.sentinel.name, "description"), self.baseline)
		out = console.read_console_output(self.execution_id)
		self.assertEqual(out["status"], "Completed")
		self.assertEqual(out["content"], "execution output")
		self.assertFalse(out["committed"])

	def test_commit_persists_and_worker_does_not_execute_twice(self):
		self.queue(commit=True)
		console.run_execution(self.execution_id)
		self.assertEqual(frappe.db.get_value("ToDo", self.sentinel.name, "description"), "MCP test changed")
		self.assertTrue(console.read_console_output(self.execution_id)["committed"])
		with patch.object(console, "run_source") as source:
			console.run_execution(self.execution_id)
			source.assert_not_called()

	def test_exception_rolls_back_requested_commit_and_records_traceback(self):
		self.queue(commit=True, fail=True)
		console.run_execution(self.execution_id)
		self.assertEqual(frappe.db.get_value("ToDo", self.sentinel.name, "description"), self.baseline)
		out = console.read_console_output(self.execution_id, part="error")
		self.assertEqual(out["status"], "Failed")
		self.assertEqual(out["error_type"], "ZeroDivisionError")
		self.assertFalse(out["committed"])
		self.assertIn("Traceback", out["content"])

	def test_revoked_permission_prevents_queued_execution(self):
		self.queue(commit=True)
		self.settings.enabled = 0
		console.run_execution(self.execution_id)
		self.assertEqual(frappe.db.get_value("ToDo", self.sentinel.name, "description"), self.baseline)
		self.assertEqual(frappe.db.get_value(console.RUN_TYPE, self.execution_id, "status"), "Failed")


def run():
	missing_energy_settings = not frappe.db.exists("DocType", "Energy Point Settings")
	energy_patch = patch(
		"frappe.social.doctype.energy_point_rule.energy_point_rule.is_energy_point_enabled",
		return_value=False,
	)
	if missing_energy_settings:
		energy_patch.start()
	try:
		suite = unittest.TestSuite(
			[
				unittest.defaultTestLoader.loadTestsFromTestCase(TestExtendedMCP),
				unittest.defaultTestLoader.loadTestsFromTestCase(TestConsoleTransactions),
			]
		)
		result = unittest.TextTestRunner(verbosity=2).run(suite)
	finally:
		if missing_energy_settings:
			energy_patch.stop()
		frappe.db.rollback()
		frappe.clear_cache(doctype="ToDo")
	if not result.wasSuccessful():
		raise RuntimeError("IGCTools MCP extended integration tests failed")
