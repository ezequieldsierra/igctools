"""OAuth authorization-code + S256 adapter using Frappe's existing OAuth storage.

MCP grants bind opaque tokens to this resource. No passwords or tokens are logged.
The dedicated client is public: proof of possession is PKCE, not a shared secret.
"""

import hashlib
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import frappe
from frappe.oauth import OAuthWebRequestValidator
from frappe.utils import add_to_date, get_datetime, now_datetime
from oauthlib.oauth2 import OAuth2Error, Server
from werkzeug.exceptions import HTTPException


def get_settings():
	if not frappe.db.exists("DocType", "IGC MCP Settings"):
		return frappe._dict(enabled=0)
	return frappe.get_cached_doc("IGC MCP Settings")


def endpoints(settings):
	base = settings.site_url.rstrip("/")
	return {
		"issuer": base + "/igctools-mcp",
		"resource": base + "/api/method/igctools.mcp.handle",
		"metadata": base + "/.well-known/oauth-protected-resource/api/method/igctools.mcp.handle",
		"authorization_endpoint": base + "/api/method/igctools.mcp_auth.authorize",
		"token_endpoint": base + "/api/method/igctools.mcp_auth.token",
	}


def check_origin(settings):
	origin = frappe.request.headers.get("Origin")
	return origin is None or origin in (settings.site_url.rstrip("/"), "https://chatgpt.com")


def before_request():
	path = frappe.request.path
	resource_path = "/.well-known/oauth-protected-resource/api/method/igctools.mcp.handle"
	issuer_path = "/.well-known/oauth-authorization-server/igctools-mcp"
	if path not in (resource_path, issuer_path):
		return
	settings = get_settings()
	if not settings.enabled:
		return
	from igctools.mcp import response

	urls = endpoints(settings)
	if frappe.request.method != "GET":
		raise HTTPException(response=response(status=405, headers={"Allow": "GET"}))
	if path == resource_path:
		body = {
			"resource": urls["resource"],
			"authorization_servers": [urls["issuer"]],
			"scopes_supported": ["all"],
			"bearer_methods_supported": ["header"],
		}
	else:
		body = {
			"issuer": urls["issuer"],
			"authorization_endpoint": urls["authorization_endpoint"],
			"token_endpoint": urls["token_endpoint"],
			"response_types_supported": ["code"],
			"grant_types_supported": ["authorization_code", "refresh_token"],
			"token_endpoint_auth_methods_supported": ["none"],
			"code_challenge_methods_supported": ["S256"],
			"scopes_supported": ["all"],
			"authorization_response_iss_parameter_supported": True,
		}
	raise HTTPException(response=response(body))


def hashed(value):
	return hashlib.sha256(value.encode("utf-8")).hexdigest()


def grant_name(kind, secret):
	return kind.lower() + "-" + hashed(secret)


def load_grant(kind, secret):
	name = grant_name(kind, secret)
	if not frappe.db.exists("IGC MCP Grant", name):
		return None
	return frappe.get_doc("IGC MCP Grant", name, for_update=kind == "Code")


def valid_grant(grant, settings, refresh=False):
	if not grant or grant.revoked or grant.resource != endpoints(settings)["resource"]:
		return False
	if grant.oauth_client != settings.oauth_client or grant.actor != settings.allowed_user:
		return False
	expires = grant.refresh_expires_on if refresh else grant.expires_on
	return bool(expires and get_datetime(expires) > now_datetime())


def authenticate(settings):
	from igctools.mcp import response
	from igctools.mcp_scripts import require_user

	header = frappe.request.headers.get("Authorization", "").split()
	valid = len(header) == 2 and header[0].lower() == "bearer"
	grant = load_grant("Token", header[1]) if valid else None
	if not valid_grant(grant, settings):
		challenge = 'Bearer resource_metadata="' + endpoints(settings)["metadata"] + '", scope="all"'
		return response({"error": "Authentication required"}, 401, {"WWW-Authenticate": challenge})
	# Native Frappe authentication validates signature/lookup, expiry and revocation first.
	row = frappe.db.get_value(
		"OAuth Bearer Token",
		header[1],
		["user", "client", "status", "expiration_time", "scopes"],
		as_dict=True,
	)
	if not row or row.status != "Active" or get_datetime(row.expiration_time) <= now_datetime():
		return response({"error": "Token expired or revoked"}, 401)
	if (
		row.user != frappe.session.user
		or row.user != grant.actor
		or row.client != settings.oauth_client
		or "all" not in row.scopes.split()
	):
		return response({"error": "Token identity or scope mismatch"}, 403)
	try:
		require_user()
	except frappe.PermissionError:
		return response({"error": "User is not authorized"}, 403)
	return None


class MCPValidator(OAuthWebRequestValidator):
	"""Restrict native OAuth to one client, one resource and one authorized user."""

	def validate_client_id(self, client_id, request, *args, **kwargs):
		from igctools.igctools.doctype.igc_mcp_settings.igc_mcp_settings import is_connector_client

		settings = get_settings()
		if not settings.enabled or client_id != settings.oauth_client:
			return False
		request.client = frappe.get_doc("OAuth Client", client_id)
		return is_connector_client(request.client)

	def client_authentication_required(self, request, *args, **kwargs):
		return False

	def authenticate_client_id(self, client_id, request, *args, **kwargs):
		return self.validate_client_id(client_id, request)

	def authenticate_client(self, request, *args, **kwargs):
		return False

	def validate_response_type(self, client_id, response_type, client, request, *args, **kwargs):
		return response_type == "code"

	def validate_grant_type(self, client_id, grant_type, client, request, *args, **kwargs):
		return grant_type in ("authorization_code", "refresh_token")

	def validate_scopes(self, client_id, scopes, client, request, *args, **kwargs):
		return set(scopes) == {"all"}

	def get_default_scopes(self, client_id, request, *args, **kwargs):
		return ["all"]

	def is_pkce_required(self, client_id, request):
		return True

	def save_authorization_code(self, client_id, code, request, *args, **kwargs):
		from igctools.mcp_scripts import require_user

		settings = require_user()
		doc = frappe.get_doc(
			{
				"doctype": "OAuth Authorization Code",
				"authorization_code": code["code"],
				"client": client_id,
				"user": frappe.session.user,
				"scopes": "all",
				"redirect_uri_bound_to_authorization_code": request.redirect_uri,
				"code_challenge": request.code_challenge,
				"code_challenge_method": "s256",
			}
		)
		doc.insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "IGC MCP Grant",
				"name": grant_name("Code", code["code"]),
				"kind": "Code",
				"oauth_client": client_id,
				"actor": frappe.session.user,
				"resource": endpoints(settings)["resource"],
				"expires_on": add_to_date(now_datetime(), minutes=5),
			}
		).insert(ignore_permissions=True)

	def validate_code(self, client_id, code, client, request, *args, **kwargs):
		grant = load_grant("Code", code)
		if not valid_grant(grant, get_settings()) or grant.oauth_client != client_id:
			return False
		row = frappe.db.get_value(
			"OAuth Authorization Code", code, ["validity", "user", "scopes"], as_dict=True
		)
		if not row or row.validity != "Valid" or row.user != grant.actor:
			return False
		request.user, request.scopes = row.user, row.scopes.split()
		return True

	def get_code_challenge(self, code, request):
		return frappe.db.get_value("OAuth Authorization Code", code, "code_challenge")

	def get_code_challenge_method(self, code, request):
		return "S256"

	def confirm_redirect_uri(self, client_id, code, redirect_uri, client, *args, **kwargs):
		return redirect_uri == frappe.db.get_value(
			"OAuth Authorization Code", code, "redirect_uri_bound_to_authorization_code"
		)

	def invalidate_authorization_code(self, client_id, code, request, *args, **kwargs):
		frappe.db.set_value("IGC MCP Grant", grant_name("Code", code), "revoked", 1)
		frappe.db.set_value("OAuth Authorization Code", code, "validity", "Invalid")

	def save_bearer_token(self, token, request, *args, **kwargs):
		from igctools.mcp_scripts import require_user

		settings = require_user(request.user)
		client_id = request.client.name
		row = frappe.get_doc(
			{
				"doctype": "OAuth Bearer Token",
				"client": client_id,
				"user": request.user,
				"scopes": "all",
				"access_token": token["access_token"],
				"refresh_token": token.get("refresh_token"),
				"expires_in": token["expires_in"],
			}
		)
		row.insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "IGC MCP Grant",
				"name": grant_name("Token", token["access_token"]),
				"kind": "Token",
				"oauth_client": client_id,
				"actor": request.user,
				"resource": endpoints(settings)["resource"],
				"expires_on": row.expiration_time,
				"refresh_hash": hashed(token["refresh_token"]) if token.get("refresh_token") else None,
				"refresh_expires_on": add_to_date(now_datetime(), days=30),
			}
		).insert(ignore_permissions=True)
		if request.grant_type == "refresh_token":
			self.invalidate_refresh_token(request)
		token["resource"] = endpoints(settings)["resource"]

	def rotate_refresh_token(self, request):
		return True

	def validate_refresh_token(self, refresh_token, client, request, *args, **kwargs):
		name = frappe.db.get_value(
			"IGC MCP Grant", {"refresh_hash": hashed(refresh_token), "revoked": 0}, "name"
		)
		grant = frappe.get_doc("IGC MCP Grant", name, for_update=True) if name else None
		if not valid_grant(grant, get_settings(), refresh=True) or grant.oauth_client != client.name:
			return False
		row = frappe.db.get_value(
			"OAuth Bearer Token",
			{"refresh_token": refresh_token, "status": "Active"},
			["user", "client"],
			as_dict=True,
		)
		if not row or row.user != grant.actor or row.client != client.name:
			return False
		request.user, request.scopes = row.user, ["all"]
		return True

	def get_original_scopes(self, refresh_token, request, *args, **kwargs):
		return ["all"]

	def invalidate_refresh_token(self, request):
		old = request.refresh_token
		if not old:
			return
		name = frappe.db.get_value("IGC MCP Grant", {"refresh_hash": hashed(old), "revoked": 0}, "name")
		if name:
			frappe.db.set_value("IGC MCP Grant", name, "revoked", 1)
		row = frappe.db.get_value("OAuth Bearer Token", {"refresh_token": old}, "name")
		if row:
			frappe.db.set_value("OAuth Bearer Token", row, "status", "Revoked")


def get_server():
	return Server(MCPValidator(), token_expires_in=3600)


def validate_request(settings, authorization=False):
	if not settings.enabled or not check_origin(settings):
		raise frappe.PermissionError("MCP authorization is unavailable.")
	params = frappe.form_dict
	if (
		params.get("resource") != endpoints(settings)["resource"]
		or params.get("client_id") != settings.oauth_client
	):
		raise frappe.PermissionError("OAuth client or resource does not match this connector.")
	if authorization:
		if params.get("response_type") != "code" or params.get("code_challenge_method") != "S256":
			raise frappe.ValidationError("Authorization code with S256 PKCE is required.")
		if not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.get("code_challenge", "")):
			raise frappe.ValidationError("Invalid S256 code challenge.")
		if not params.get("state"):
			raise frappe.ValidationError("OAuth state is required.")


@frappe.whitelist(allow_guest=True, methods=["GET"])
def authorize(**kwargs):
	settings = get_settings()
	validate_request(settings, authorization=True)
	server = get_server()
	scopes, credentials = server.validate_authorization_request(
		frappe.request.url, "GET", headers=frappe.request.headers
	)
	if frappe.session.user == "Guest":
		frappe.local.response.update(
			{"type": "redirect", "location": "/login?" + urlencode({"redirect-to": frappe.request.url})}
		)
		return
	from igctools.mcp_scripts import require_user

	require_user()
	params = {
		key: value
		for key, value in kwargs.items()
		if key
		in (
			"client_id",
			"redirect_uri",
			"response_type",
			"scope",
			"state",
			"code_challenge",
			"code_challenge_method",
			"resource",
		)
	}
	success = "/api/method/igctools.mcp_auth.approve?" + urlencode(params)
	failure = append_params(
		credentials["redirect_uri"],
		{"error": "access_denied", "state": params["state"], "iss": endpoints(settings)["issuer"]},
	)
	from frappe.sessions import get_csrf_token

	html = frappe.render_template(
		"templates/igctools_mcp_consent.html",
		{"success_url": success, "failure_url": failure, "csrf_token": get_csrf_token()},
	)
	frappe.respond_as_web_page("Connect IGCTools", html, primary_action=None)


def append_params(url, values):
	parts = urlsplit(url)
	query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in values]
	return urlunsplit(
		(parts.scheme, parts.netloc, parts.path, urlencode(query + list(values.items())), parts.fragment)
	)


@frappe.whitelist(methods=["POST"])
def approve(**kwargs):
	from igctools.mcp_scripts import require_user

	settings = require_user()
	validate_request(settings, authorization=True)
	server = get_server()
	scopes, credentials = server.validate_authorization_request(
		frappe.request.url, "POST", headers=frappe.request.headers
	)
	headers, body, status = server.create_authorization_response(
		frappe.request.url, "POST", headers=frappe.request.headers, scopes=scopes, credentials=credentials
	)
	location = append_params(headers["Location"], {"iss": endpoints(settings)["issuer"]})
	frappe.local.response.update({"type": "redirect", "location": location})


@frappe.whitelist(allow_guest=True, methods=["POST"])
def token(**kwargs):
	from igctools.mcp import response

	settings = get_settings()
	validate_request(settings)
	if frappe.request.mimetype != "application/x-www-form-urlencoded":
		return response({"error": "invalid_request"}, 415)
	if frappe.form_dict.get("grant_type") not in ("authorization_code", "refresh_token"):
		return response({"error": "unsupported_grant_type"}, 400)
	frappe.db.savepoint("igctools_mcp_oauth")
	try:
		headers, body, status = get_server().create_token_response(
			frappe.request.url,
			"POST",
			body=frappe.request.get_data(as_text=True),
			headers=frappe.request.headers,
		)
		if status >= 400:
			frappe.db.rollback(save_point="igctools_mcp_oauth")
		return response(json.loads(body), status, headers)
	except Exception:
		frappe.db.rollback(save_point="igctools_mcp_oauth")
		return response({"error": "invalid_grant"}, 400)
