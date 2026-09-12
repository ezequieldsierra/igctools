import frappe
from frappe.model.document import Document


class IGCMCPChange(Document):
	def validate(self):
		if not self.flags.igctools_mcp_write:
			raise frappe.PermissionError("MCP backups are written by the connector only.")

	def on_trash(self):
		raise frappe.PermissionError("MCP backups cannot be deleted from Desk or the API.")
