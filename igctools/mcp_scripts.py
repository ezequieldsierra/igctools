"""Script operations shared by the MCP endpoint. No script execution tool."""

import hashlib
import json

import frappe

SCRIPT_TYPES = ("Client Script", "Server Script")
MAX_SCRIPT_BYTES = 1000000


def serialize(value):
	return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def digest(value):
	return hashlib.sha256(value.encode("utf-8")).hexdigest()


def snapshot(doc):
	return serialize(doc.as_dict())


def require_user(user=None):
	from igctools.mcp_auth import get_settings

	settings = get_settings()
	user = user or frappe.session.user
	if not settings.enabled or user in (None, "", "Guest") or user != settings.allowed_user:
		raise frappe.PermissionError("This user is not authorized for IGCTools MCP.")
	if not frappe.db.get_value("User", user, "enabled"):
		raise frappe.PermissionError("The authorized user is disabled.")
	if user != "Administrator" and "System Manager" not in frappe.get_roles(user):
		raise frappe.PermissionError("System Manager is required.")
	return settings


def get_script_doc(script_type, name, write=False):
	require_user()
	if script_type not in SCRIPT_TYPES:
		raise frappe.ValidationError("Only Client Script and Server Script are supported.")
	doc = frappe.get_doc(script_type, name, for_update=write)
	doc.check_permission("write" if write else "read")
	return doc


def metadata(doc):
	return {
		"script_type": doc.doctype,
		"name": doc.name,
		"reference_doctype": doc.get("dt") or doc.get("reference_doctype"),
		"enabled": bool(doc.get("enabled"))
		if doc.doctype == "Client Script"
		else not bool(doc.get("disabled")),
		"modified": str(doc.modified),
		"revision": digest(snapshot(doc)),
	}


def connection_info():
	require_user()
	return {
		"site": frappe.local.site,
		"user": frappe.session.user,
		"frappe_version": frappe.__version__,
		"supported_documents": list(SCRIPT_TYPES),
		"executes_scripts": False,
	}


def search_scripts(script_type, query="", offset=0, limit=30):
	require_user()
	if script_type not in SCRIPT_TYPES:
		raise frappe.ValidationError("Unsupported script type.")
	filters = [["name", "like", "%" + query + "%"]] if query else []
	fields = (
		["name", "modified", "dt", "enabled", "view"]
		if script_type == "Client Script"
		else ["name", "modified", "reference_doctype", "disabled", "script_type", "api_method"]
	)
	rows = frappe.get_list(
		script_type, filters=filters, fields=fields, order_by="name asc", start=offset, page_length=limit + 1
	)
	return {"scripts": rows[:limit], "next_offset": offset + limit if len(rows) > limit else None}


def read_script(script_type, name, start_line=1, line_count=200):
	doc = get_script_doc(script_type, name)
	lines = (doc.script or "").splitlines(keepends=True)
	result = metadata(doc)
	result.update(
		{
			"script": "".join(lines[start_line - 1 : start_line - 1 + line_count]),
			"start_line": start_line,
			"total_lines": len(lines),
			"next_line": start_line + line_count if start_line - 1 + line_count < len(lines) else None,
		}
	)
	return result


def validate_script(script_type, script):
	if len(script.encode("utf-8")) > MAX_SCRIPT_BYTES:
		raise frappe.ValidationError("Script exceeds the one megabyte limit.")
	if script_type == "Server Script":
		from frappe.utils.safe_exec import FrappeTransformer
		from RestrictedPython import compile_restricted

		# Compilation only: never execute the submitted source.
		compile_restricted(script, filename="<IGCTools MCP validation>", policy=FrappeTransformer)


def check_revision(doc, expected_revision):
	if digest(snapshot(doc)) != expected_revision:
		raise frappe.TimestampMismatchError("Script changed since it was read. Read it again before editing.")


def save_change(doc, script, reason, restored_from=None):
	validate_script(doc.doctype, script)
	if script == (doc.script or ""):
		return {**metadata(doc), "changed": False, "audit_id": None}
	before = snapshot(doc)
	audit = frappe.get_doc(
		{
			"doctype": "IGC MCP Change",
			"script_type": doc.doctype,
			"script_name": doc.name,
			"change_reason": reason,
			"actor": frappe.session.user,
			"before_snapshot": before,
			"before_hash": digest(before),
			"restored_from": restored_from,
		}
	)
	# The archive is a non-executable document, written in the same DB transaction.
	audit.flags.igctools_mcp_write = True
	audit.insert(ignore_permissions=True)
	doc.script = script
	doc.save()
	verified = frappe.get_doc(doc.doctype, doc.name)
	if verified.script != script:
		raise frappe.ValidationError("Saved script does not match the requested source.")
	after = snapshot(verified)
	audit.after_snapshot = after
	audit.after_hash = digest(after)
	audit.save(ignore_permissions=True)
	return {
		**metadata(verified),
		"changed": True,
		"audit_id": audit.name,
		"validation": "restricted_python_compilation"
		if doc.doctype == "Server Script"
		else "saved_source_verified",
		"behavior_tested": False,
	}


def update_script(script_type, name, expected_revision, script, reason):
	doc = get_script_doc(script_type, name, write=True)
	check_revision(doc, expected_revision)
	return save_change(doc, script, reason)


def edit_script(script_type, name, expected_revision, old_text, new_text, reason):
	doc = get_script_doc(script_type, name, write=True)
	check_revision(doc, expected_revision)
	if not old_text or (doc.script or "").count(old_text) != 1:
		raise frappe.ValidationError("old_text must match exactly once in the current script.")
	return save_change(doc, doc.script.replace(old_text, new_text, 1), reason)


def script_history(script_type, name, limit=20):
	get_script_doc(script_type, name)
	return {
		"changes": frappe.get_list(
			"IGC MCP Change",
			filters={"script_type": script_type, "script_name": name},
			fields=[
				"name",
				"creation",
				"actor",
				"change_reason",
				"before_hash",
				"after_hash",
				"restored_from",
			],
			order_by="creation desc",
			page_length=limit,
		)
	}


def restore_script(script_type, name, expected_revision, audit_id, reason):
	doc = get_script_doc(script_type, name, write=True)
	check_revision(doc, expected_revision)
	audit = frappe.get_doc("IGC MCP Change", audit_id)
	audit.check_permission("read")
	if audit.script_type != script_type or audit.script_name != name:
		raise frappe.ValidationError("Backup belongs to a different script.")
	if digest(audit.before_snapshot) != audit.before_hash:
		raise frappe.ValidationError("Backup integrity check failed.")
	before = json.loads(audit.before_snapshot)
	if before.get("doctype") != script_type or before.get("name") != name:
		raise frappe.ValidationError("Backup identity check failed.")
	return save_change(doc, before.get("script") or "", reason, restored_from=audit.name)
