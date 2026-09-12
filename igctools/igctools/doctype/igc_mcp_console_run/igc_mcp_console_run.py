import frappe
from frappe.model.document import Document


class IGCMCPConsoleRun(Document):
	def validate(self):
		if not self.flags.igctools_mcp_write:
			raise frappe.PermissionError("Console executions are written by the connector only.")

	def on_trash(self):
		raise frappe.PermissionError("Console executions cannot be deleted from Desk or the API.")
