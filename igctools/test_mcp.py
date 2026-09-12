"""Integration tests using real Frappe documents; every test rolls back its data.

Run through the documented test runner after syncing the three new DocTypes.
No business script is executed or modified.
"""

import base64
import copy
import hashlib
import json
import unittest
import uuid
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

import frappe
from werkzeug.exceptions import HTTPException
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from igctools import mcp
from igctools import mcp_auth as auth
from igctools import mcp_scripts as scripts
from igctools.igctools.doctype.igc_mcp_settings.igc_mcp_settings import CALLBACK


class TestScriptMCP(unittest.TestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.savepoint("igctools_mcp_test")
		self.addCleanup(lambda: frappe.db.rollback(save_point="igctools_mcp_test"))
		self.client = frappe.get_doc(
			{
				"doctype": "OAuth Client",
				"app_name": "IGCTools MCP Test",
				"token_endpoint_auth_method": "None",
				"grant_type": "Authorization Code",
				"response_type": "Code",
				"redirect_uris": CALLBACK,
				"default_redirect_uri": CALLBACK,
				"scopes": "all",
			}
		).insert()
		self.settings = frappe._dict(
			enabled=1,
			allowed_user="Administrator",
			site_url="https://vias.cloud",
			oauth_client=self.client.name,
		)
		self.settings_patch = patch.object(auth, "get_settings", return_value=self.settings)
		self.settings_patch.start()
		self.addCleanup(self.settings_patch.stop)
		self.doc = frappe.get_doc(
			{
				"doctype": "Client Script",
				"name": "IGCTools MCP Test " + uuid.uuid4().hex,
				"dt": "ToDo",
				"view": "Form",
				"enabled": 0,
				"script": "// original\nconst value = 1;\n",
			}
		).insert()
		self.set_request()

	def tearDown(self):
		frappe.set_user("Administrator")

	def set_request(
		self, path="/api/method/igctools.mcp.handle", body=None, headers=None, method="POST", form=None
	):
		headers = {"Accept": "application/json, text/event-stream", **(headers or {})}
		if form is not None:
			builder = EnvironBuilder(
				path=path, base_url="https://vias.cloud", method=method, data=form, headers=headers
			)
		else:
			builder = EnvironBuilder(
				path=path,
				base_url="https://vias.cloud",
				method=method,
				data=json.dumps(body or {"jsonrpc": "2.0", "id": 1, "method": "ping"}),
				content_type="application/json",
				headers=headers,
			)
		frappe.local.request = Request(builder.get_environ())
		frappe.local.form_dict = frappe._dict(form or {})

	def invoke(self, name, args):
		self.set_request(
			body={
				"jsonrpc": "2.0",
				"id": 1,
				"method": "tools/call",
				"params": {"name": name, "arguments": args},
			}
		)
		with patch.object(auth, "authenticate", return_value=None):
			return mcp.handle().get_json()

	def identity(self):
		return {"script_type": "Client Script", "name": self.doc.name}

	def edit_args(self):
		return {
			**self.identity(),
			"expected_revision": scripts.read_script(**self.identity())["revision"],
			"old_text": "value = 1",
			"new_text": "value = 2",
			"reason": "Integration test",
		}

	def test_edit_preserves_state_and_archives_exact_original(self):
		before = scripts.snapshot(frappe.get_doc("Client Script", self.doc.name))
		result = scripts.edit_script(**self.edit_args())
		changed = frappe.get_doc("Client Script", self.doc.name)
		self.assertEqual(changed.enabled, 0)
		self.assertEqual(changed.dt, "ToDo")
		self.assertIn("value = 2", changed.script)
		audit = frappe.get_doc("IGC MCP Change", result["audit_id"])
		self.assertEqual(audit.before_snapshot, before)
		self.assertEqual(audit.before_hash, scripts.digest(before))
		self.assertEqual(audit.after_snapshot, scripts.snapshot(changed))

	def test_stale_revision_cannot_overwrite_newer_source(self):
		args = self.edit_args()
		frappe.db.set_value("Client Script", self.doc.name, "script", "// concurrent change")
		with self.assertRaises(frappe.TimestampMismatchError):
			scripts.edit_script(**args)
		self.assertEqual(
			frappe.db.get_value("Client Script", self.doc.name, "script"), "// concurrent change"
		)

	def test_restore_creates_another_backup(self):
		changed = scripts.edit_script(**self.edit_args())
		result = scripts.restore_script(
			**self.identity(),
			expected_revision=changed["revision"],
			audit_id=changed["audit_id"],
			reason="Restore test",
		)
		self.assertEqual(frappe.db.get_value("Client Script", self.doc.name, "script"), self.doc.script)
		self.assertNotEqual(changed["audit_id"], result["audit_id"])

	def test_restore_rejects_corrupt_backup(self):
		changed = scripts.edit_script(**self.edit_args())
		frappe.db.set_value("IGC MCP Change", changed["audit_id"], "before_snapshot", "{}")
		with self.assertRaises(frappe.ValidationError):
			scripts.restore_script(
				**self.identity(),
				expected_revision=changed["revision"],
				audit_id=changed["audit_id"],
				reason="Corrupt test",
			)

	def test_backup_is_not_editable_through_document_save(self):
		changed = scripts.edit_script(**self.edit_args())
		audit = frappe.get_doc("IGC MCP Change", changed["audit_id"])
		audit.change_reason = "tamper"
		with self.assertRaises(frappe.PermissionError):
			audit.save()

	def test_ambiguous_match_rejected(self):
		args = self.edit_args()
		args["old_text"] = "\n"
		with self.assertRaises(frappe.ValidationError):
			scripts.edit_script(**args)

	def test_failure_rolls_back_source_and_backup(self):
		from frappe.custom.doctype.client_script.client_script import ClientScript

		before = frappe.db.count("IGC MCP Change")
		with patch.object(ClientScript, "on_update", side_effect=RuntimeError("forced test failure")):
			result = self.invoke("edit_script", self.edit_args())
		self.assertTrue(result["result"]["isError"])
		self.assertEqual(frappe.db.count("IGC MCP Change"), before)
		self.assertEqual(frappe.db.get_value("Client Script", self.doc.name, "script"), self.doc.script)

	def test_server_script_compilation_does_not_execute(self):
		scripts.validate_script("Server Script", "raise Exception('this must never execute')")
		with self.assertRaises(SyntaxError):
			scripts.validate_script("Server Script", "if:")

	def test_guest_denied_by_service(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			scripts.read_script(**self.identity())

	def test_arbitrary_doctype_denied(self):
		with self.assertRaises(frappe.ValidationError):
			scripts.get_script_doc("User", "Administrator")

	def test_http_requires_bearer_even_with_session(self):
		result = mcp.handle()
		self.assertEqual(result.status_code, 401)
		self.assertIn("resource_metadata", result.headers["WWW-Authenticate"])

	def test_http_origin_and_disabled_gate(self):
		self.set_request(headers={"Origin": "https://attacker.invalid"})
		self.assertEqual(mcp.handle().status_code, 403)
		self.settings.enabled = 0
		self.assertEqual(mcp.handle().status_code, 404)

	def test_protocol_initialize_and_no_stream(self):
		self.set_request(
			body={
				"jsonrpc": "2.0",
				"id": 1,
				"method": "initialize",
				"params": {"protocolVersion": "2099-01-01"},
			}
		)
		with patch.object(auth, "authenticate", return_value=None):
			self.assertEqual(mcp.handle().get_json()["result"]["protocolVersion"], "2025-06-18")
			self.set_request(method="GET")
			self.assertEqual(mcp.handle().status_code, 405)

	def test_notification_cannot_execute_write(self):
		self.set_request(
			body={
				"jsonrpc": "2.0",
				"method": "tools/call",
				"params": {"name": "edit_script", "arguments": self.edit_args()},
			}
		)
		with patch.object(auth, "authenticate", return_value=None):
			self.assertEqual(mcp.handle().status_code, 400)
		self.assertEqual(frappe.db.get_value("Client Script", self.doc.name, "script"), self.doc.script)

	def test_schema_rejects_extra_arguments_and_bool_integer(self):
		result = self.invoke("connection_info", {"command": "unavailable"})
		self.assertEqual(result["error"]["code"], -32602)
		result = self.invoke("read_script", {**self.identity(), "line_count": True})
		self.assertEqual(result["error"]["code"], -32602)

	def test_resource_metadata_uses_scoped_issuer(self):
		self.set_request(path="/.well-known/oauth-authorization-server/igctools-mcp", method="GET")
		with self.assertRaises(HTTPException) as caught:
			auth.before_request()
		body = caught.exception.response.get_json()
		self.assertEqual(body["code_challenge_methods_supported"], ["S256"])
		self.assertEqual(body["issuer"], "https://vias.cloud/igctools-mcp")

	def create_code(self):
		verifier = "v" * 64
		challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
		params = {
			"client_id": self.client.name,
			"redirect_uri": CALLBACK,
			"response_type": "code",
			"scope": "all",
			"state": "test-state",
			"code_challenge": challenge,
			"code_challenge_method": "S256",
			"resource": auth.endpoints(self.settings)["resource"],
		}
		url = "https://vias.cloud/api/method/igctools.mcp_auth.authorize?" + urlencode(params)
		server = auth.get_server()
		scopes, credentials = server.validate_authorization_request(url)
		headers, body, status = server.create_authorization_response(
			url, scopes=scopes, credentials=credentials
		)
		self.assertEqual(status, 302)
		return parse_qs(urlsplit(headers["Location"]).query)["code"][0], verifier

	def exchange(self, code, verifier):
		params = {
			"client_id": self.client.name,
			"grant_type": "authorization_code",
			"code": code,
			"code_verifier": verifier,
			"redirect_uri": CALLBACK,
			"resource": auth.endpoints(self.settings)["resource"],
		}
		headers, body, status = auth.get_server().create_token_response(
			"https://vias.cloud/api/method/igctools.mcp_auth.token", body=urlencode(params)
		)
		return json.loads(body), status

	def test_oauth_pkce_exchange_resource_and_native_authentication(self):
		code, verifier = self.create_code()
		value, status = self.exchange(code, verifier)
		self.assertEqual(status, 200)
		self.assertEqual(value["resource"], auth.endpoints(self.settings)["resource"])
		self.set_request(headers={"Authorization": "Bearer " + value["access_token"]})
		frappe.set_user("Guest")
		from frappe.auth import validate_auth

		validate_auth()
		self.assertEqual(frappe.session.user, "Administrator")
		self.assertIsNone(auth.authenticate(self.settings))
		self.assertEqual(mcp.handle().status_code, 200)

	def test_oauth_wrong_verifier_rejected(self):
		code, verifier = self.create_code()
		value, status = self.exchange(code, "x" * 64)
		self.assertEqual(status, 400)
		self.assertNotIn("access_token", value)

	def test_oauth_code_cannot_be_reused(self):
		code, verifier = self.create_code()
		self.assertEqual(self.exchange(code, verifier)[1], 200)
		self.assertEqual(self.exchange(code, verifier)[1], 400)

	def test_oauth_token_for_wrong_resource_rejected(self):
		code, verifier = self.create_code()
		value, status = self.exchange(code, verifier)
		frappe.db.set_value(
			"IGC MCP Grant",
			auth.grant_name("Token", value["access_token"]),
			"resource",
			"https://other.invalid/mcp",
		)
		self.set_request(headers={"Authorization": "Bearer " + value["access_token"]})
		self.assertEqual(auth.authenticate(self.settings).status_code, 401)

	def test_oauth_refresh_rotates_and_revokes_previous_token(self):
		code, verifier = self.create_code()
		first, status = self.exchange(code, verifier)
		params = {
			"grant_type": "refresh_token",
			"refresh_token": first["refresh_token"],
			"client_id": self.client.name,
			"resource": auth.endpoints(self.settings)["resource"],
		}
		headers, body, status = auth.get_server().create_token_response(
			"https://vias.cloud/api/method/igctools.mcp_auth.token", body=urlencode(params)
		)
		self.assertEqual(status, 200)
		second = json.loads(body)
		self.assertNotEqual(first["access_token"], second["access_token"])
		self.assertEqual(
			frappe.db.get_value("OAuth Bearer Token", first["access_token"], "status"), "Revoked"
		)
		self.assertEqual(
			frappe.db.get_value("OAuth Bearer Token", second["access_token"], "user"), "Administrator"
		)

	def test_settings_create_compatible_public_client(self):
		settings = frappe.get_doc("IGC MCP Settings")
		settings.enabled, settings.allowed_user, settings.site_url = 1, "Administrator", "https://vias.cloud"
		settings.oauth_client = None
		settings.validate()
		client = frappe.get_doc("OAuth Client", settings.oauth_client)
		if client.meta.has_field("token_endpoint_auth_method"):
			self.assertEqual(client.token_endpoint_auth_method, "None")
		self.assertEqual(settings.oauth_client_id, client.client_id)
		self.assertEqual(settings.server_url, "https://vias.cloud/api/method/igctools.mcp.handle")

	def standard_oauth_schema(self):
		from frappe.integrations.doctype.oauth_client.oauth_client import OAuthClient

		meta = copy.deepcopy(frappe.get_meta("OAuth Client"))
		meta.fields = [f for f in meta.fields if f.fieldname != "token_endpoint_auth_method"]
		meta.__dict__.pop("_valid_columns", None)
		meta.init_field_caches()
		return patch.object(OAuthClient, "meta", meta)

	def test_standard_oauth_schema_saves_settings_and_authorizes_with_pkce(self):
		# Reproduce production's standard v15 schema without changing this site's schema.
		with self.standard_oauth_schema():
			settings = frappe.get_doc("IGC MCP Settings")
			settings.enabled, settings.allowed_user, settings.site_url = (
				1,
				"Administrator",
				"https://vias.cloud",
			)
			settings.oauth_client = None
			settings.save()
			saved = frappe.get_doc("IGC MCP Settings")
			self.client = frappe.get_doc("OAuth Client", saved.oauth_client)
			self.assertFalse(self.client.meta.has_field("token_endpoint_auth_method"))
			self.assertEqual(saved.oauth_client_id, self.client.client_id)
			self.assertEqual(saved.server_url, auth.endpoints(self.settings)["resource"])
			self.settings.oauth_client = self.client.name
			self.test_oauth_pkce_exchange_resource_and_native_authentication()
			frappe.set_user("Administrator")
			self.test_oauth_refresh_rotates_and_revokes_previous_token()
			self.test_oauth_wrong_verifier_rejected()

	def test_standard_oauth_schema_still_rejects_changed_client_configuration(self):
		with self.standard_oauth_schema():
			for field, value in (
				("redirect_uris", CALLBACK + " https://other.invalid/callback"),
				("default_redirect_uri", "https://other.invalid/callback"),
				("grant_type", "Implicit"),
				("scopes", "all openid"),
				("skip_authorization", 1),
			):
				with self.subTest(field=field):
					original = frappe.db.get_value("OAuth Client", self.client.name, field)
					frappe.db.set_value("OAuth Client", self.client.name, field, value)
					self.assertFalse(auth.MCPValidator().validate_client_id(self.client.name, frappe._dict()))
					frappe.db.set_value("OAuth Client", self.client.name, field, original)

	def test_public_token_endpoint_as_guest(self):
		code, verifier = self.create_code()
		params = {
			"client_id": self.client.name,
			"grant_type": "authorization_code",
			"code": code,
			"code_verifier": verifier,
			"redirect_uri": CALLBACK,
			"resource": auth.endpoints(self.settings)["resource"],
		}
		frappe.set_user("Guest")
		self.set_request(path="/api/method/igctools.mcp_auth.token", form=params)
		result = auth.token()
		self.assertEqual(result.status_code, 200)
		self.assertIn("access_token", result.get_json())

	def test_token_endpoint_rejects_wrong_resource(self):
		self.set_request(
			path="/api/method/igctools.mcp_auth.token",
			form={"client_id": self.client.name, "resource": "https://other.invalid"},
		)
		with self.assertRaises(frappe.PermissionError):
			auth.token()

	def test_approval_redirect_has_issuer_and_state(self):
		challenge = base64.urlsafe_b64encode(hashlib.sha256(b"v" * 64).digest()).decode().rstrip("=")
		params = {
			"client_id": self.client.name,
			"redirect_uri": CALLBACK,
			"response_type": "code",
			"scope": "all",
			"state": "test-state",
			"code_challenge": challenge,
			"code_challenge_method": "S256",
			"resource": auth.endpoints(self.settings)["resource"],
		}
		self.set_request(
			path="/api/method/igctools.mcp_auth.approve?" + urlencode(params),
			form={"csrf_token": "test-only"},
		)
		frappe.local.form_dict.update(params)
		frappe.local.response = frappe._dict()
		auth.approve()
		query = parse_qs(urlsplit(frappe.local.response.location).query)
		self.assertEqual(query["iss"], ["https://vias.cloud/igctools-mcp"])
		self.assertEqual(query["state"], ["test-state"])
		self.assertIn("code", query)

	def test_disabled_server_script_edit_and_invalid_source_rejected(self):
		name = "IGCTools MCP Server Test " + uuid.uuid4().hex
		frappe.get_doc(
			{
				"doctype": "Server Script",
				"name": name,
				"script_type": "API",
				"disabled": 1,
				"api_method": "igctools_mcp_test_" + uuid.uuid4().hex,
				"script": "value = 1",
			}
		).insert()
		identity = {"script_type": "Server Script", "name": name}
		revision = scripts.read_script(**identity)["revision"]
		result = scripts.edit_script(
			**identity,
			expected_revision=revision,
			old_text="value = 1",
			new_text="value = 2",
			reason="Server test",
		)
		self.assertEqual(frappe.db.get_value("Server Script", name, "disabled"), 1)
		failed = self.invoke(
			"update_script",
			{
				**identity,
				"expected_revision": result["revision"],
				"script": "if:",
				"reason": "Invalid syntax test",
			},
		)
		self.assertTrue(failed["result"]["isError"])
		self.assertEqual(frappe.db.get_value("Server Script", name, "script"), "value = 2")


def run():
	# Some stripped development sites omit this unrelated social module's Single.
	# Keep that environment issue out of connector tests without changing the site.
	missing_energy_settings = not frappe.db.exists("DocType", "Energy Point Settings")
	if missing_energy_settings:
		print(
			"Test environment: Energy Point Settings is absent; its hook is disabled only in this test process."
		)
		energy_patch = patch(
			"frappe.social.doctype.energy_point_rule.energy_point_rule.is_energy_point_enabled",
			return_value=False,
		)
		energy_patch.start()
	try:
		result = unittest.TextTestRunner(verbosity=2).run(
			unittest.defaultTestLoader.loadTestsFromTestCase(TestScriptMCP)
		)
	finally:
		if missing_energy_settings:
			energy_patch.stop()
	frappe.db.rollback()
	if not result.wasSuccessful():
		raise RuntimeError("IGCTools MCP integration tests failed")
