from urllib.parse import urlsplit

import frappe
from frappe.model.document import Document

CALLBACK = "https://chatgpt.com/connector_platform_oauth_redirect"


class IGCMCPSettings(Document):
	def validate(self):
		if not self.enabled:
			return
		self.site_url = (self.site_url or frappe.utils.get_url()).rstrip("/")
		url = urlsplit(self.site_url)
		if url.scheme != "https" or not url.hostname or url.path or url.query or url.fragment or url.username:
			frappe.throw("Site URL must be a public HTTPS origin, for example https://igcaribe.com.")
		if not self.allowed_user or self.allowed_user == "Guest":
			frappe.throw("Select the authorized Frappe user.")
		user = frappe.get_doc("User", self.allowed_user)
		if not user.enabled or user.user_type != "System User":
			frappe.throw("The authorized user must be an enabled System User.")
		if user.name != "Administrator" and not {"System Manager", "Script Manager"}.issubset(
			set(frappe.get_roles(user.name))
		):
			frappe.throw("The authorized user needs System Manager and Script Manager.")
		if not self.oauth_client:
			client = frappe.get_doc(
				{
					"doctype": "OAuth Client",
					"app_name": "IGCTools ChatGPT",
					"token_endpoint_auth_method": "None",
					"grant_type": "Authorization Code",
					"response_type": "Code",
					"redirect_uris": CALLBACK,
					"default_redirect_uri": CALLBACK,
					"scopes": "all",
					"skip_authorization": 0,
				}
			)
			client.insert()
			self.oauth_client = client.name
		client = frappe.get_doc("OAuth Client", self.oauth_client)
		if (
			client.get("token_endpoint_auth_method") != "None"
			or client.default_redirect_uri != CALLBACK
			or client.redirect_uris.strip() != CALLBACK
		):
			frappe.throw("Use a dedicated public OAuth client with the ChatGPT callback URL.")
		self.oauth_client_id = client.client_id
		self.server_url = self.site_url + "/api/method/igctools.mcp.handle"
