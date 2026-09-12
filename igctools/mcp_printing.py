"""Permission-checked print formats, previews and default-format changes."""

import json

import frappe

from igctools import mcp_scripts as scripts

STYLE_FIELDS = {
	"font",
	"font_size",
	"margin_top",
	"margin_bottom",
	"margin_left",
	"margin_right",
	"page_number",
	"default_print_language",
	"align_labels_right",
	"show_section_headings",
	"line_breaks",
	"absolute_value",
}


def require_doctype(doc_type):
	scripts.require_user()
	frappe.get_doc("DocType", doc_type).check_permission("read")
	frappe.has_permission(doc_type, "read", throw=True)
	return frappe.get_meta(doc_type)


def get_format(name, write=False):
	scripts.require_user()
	doc = frappe.get_doc("Print Format", name, for_update=write)
	doc.check_permission("write" if write else "read")
	if doc.print_format_for != "DocType" or not doc.doc_type:
		frappe.throw("Only document print formats are supported.")
	require_doctype(doc.doc_type)
	return doc


def format_metadata(doc):
	return {
		"name": doc.name,
		"doc_type": doc.doc_type,
		"modified": str(doc.modified),
		"disabled": bool(doc.disabled),
		"standard": doc.standard,
		"revision": scripts.digest(scripts.snapshot(doc)),
	}


def default_state(doc_type):
	base = frappe.get_doc("DocType", doc_type)
	setters = frappe.get_list(
		"Property Setter",
		filters={"doc_type": doc_type, "doctype_or_field": "DocType", "property": "default_print_format"},
		fields=["*"],
		order_by="name asc",
		page_length=100,
	)
	return {
		"doc_type": doc_type,
		"base_default": base.get("default_print_format"),
		"property_setters": setters,
	}


def default_metadata(doc_type):
	state = default_state(doc_type)
	return {
		"default_print_format": frappe.get_meta(doc_type, cached=False).get("default_print_format")
		or "Standard",
		"default_revision": scripts.digest(scripts.serialize(state)),
	}


def archive(kind, name, before, after, reason, restored_from=None):
	before_text, after_text = scripts.serialize(before), scripts.serialize(after)
	doc = frappe.get_doc(
		{
			"doctype": "IGC MCP Change",
			"script_type": kind,
			"script_name": name,
			"actor": frappe.session.user,
			"change_reason": reason,
			"before_snapshot": before_text,
			"after_snapshot": after_text,
			"before_hash": scripts.digest(before_text),
			"after_hash": scripts.digest(after_text),
			"restored_from": restored_from,
		}
	)
	doc.flags.igctools_mcp_write = True
	doc.insert(ignore_permissions=True)
	return doc.name


def get_print_context(doc_type):
	meta = require_doctype(doc_type)
	keys = ("fieldname", "label", "fieldtype", "options", "reqd", "precision")

	def fields(value):
		return [
			{key: field.get(key) for key in keys} for field in value.fields if field.fieldtype != "Password"
		]

	children = {}
	for field in meta.fields:
		if field.fieldtype in ("Table", "Table MultiSelect") and field.options:
			children[field.fieldname] = fields(frappe.get_meta(field.options))
	samples = (
		[]
		if meta.issingle or meta.istable
		else frappe.get_list(
			doc_type, fields=["name", "modified", "docstatus"], order_by="modified desc", page_length=3
		)
	)
	return {
		"doc_type": doc_type,
		**default_metadata(doc_type),
		"fields": fields(meta),
		"child_tables": children,
		"sample_documents": samples,
		"formats": search_print_formats(doc_type=doc_type, limit=50)["formats"],
		"letter_heads": frappe.get_list("Letter Head", fields=["name", "is_default"], page_length=50),
	}


def search_print_formats(doc_type="", query="", offset=0, limit=30):
	scripts.require_user()
	filters = [["print_format_for", "=", "DocType"]]
	if doc_type:
		require_doctype(doc_type)
		filters.append(["doc_type", "=", doc_type])
	if query:
		filters.append(["name", "like", "%" + query + "%"])
	rows = frappe.get_list(
		"Print Format",
		filters=filters,
		fields=["name", "doc_type", "standard", "disabled", "custom_format", "modified"],
		order_by="name asc",
		start=offset,
		page_length=limit + 1,
	)
	return {"formats": rows[:limit], "next_offset": offset + limit if len(rows) > limit else None}


def read_print_format(name, offset=0, length=30000):
	doc = get_format(name)
	content = json.dumps(doc.as_dict(), ensure_ascii=False, indent=2, default=str)
	return {
		**format_metadata(doc),
		"document_json": content[offset : offset + length],
		"offset": offset,
		"total_characters": len(content),
		"next_offset": offset + length if offset + length < len(content) else None,
	}


def save_print_format(name, doc_type, html, css, reason, expected_revision="", settings_json="{}"):
	meta = require_doctype(doc_type)
	settings = json.loads(settings_json)
	if not isinstance(settings, dict) or set(settings) - STYLE_FIELDS:
		frappe.throw("Unknown print format style settings.")
	if any(type(value) not in (str, int, float, bool) for value in settings.values()):
		frappe.throw("Style settings must contain scalar values.")
	if len(html.encode()) + len(css.encode()) > 1000000:
		frappe.throw("Print format source exceeds one megabyte.")
	before = None
	if frappe.db.exists("Print Format", name):
		doc = get_format(name, write=True)
		scripts.check_revision(doc, expected_revision)
		if (
			doc.doc_type != doc_type
			or doc.standard != "No"
			or not doc.custom_format
			or doc.raw_printing
			or doc.print_format_type == "JS"
			or doc.print_format_builder_beta
		):
			frappe.throw(
				"Create a separate custom Jinja format; this format has a different type or builder."
			)
		before = doc.as_dict()
	else:
		if expected_revision:
			frappe.throw("The expected print format no longer exists.", frappe.TimestampMismatchError)
		doc = frappe.get_doc(
			{
				"doctype": "Print Format",
				"name": name,
				"doc_type": doc_type,
				"print_format_for": "DocType",
				"standard": "No",
				"custom_format": 1,
				"print_format_type": "Jinja",
				"module": meta.module,
				"disabled": 0,
			}
		)
		doc.check_permission("create")
	doc.update({"html": html, "css": css, **settings})
	if before is None:
		doc.insert(set_name=name)
	else:
		doc.save()
	verified = get_format(name)
	if verified.html != html or (verified.css or "") != css:
		frappe.throw("Saved print format does not match the requested source.")
	audit_id = archive("Print Format", name, before, verified.as_dict(), reason)
	return {
		**format_metadata(verified),
		"audit_id": audit_id,
		"validation": "jinja_syntax_and_saved_source",
		"behavior_tested": False,
	}


def set_default_print_format(name, expected_revision, expected_default_revision, reason):
	doc = get_format(name, write=True)
	scripts.check_revision(doc, expected_revision)
	if doc.disabled:
		frappe.throw("A disabled print format cannot be the default.")
	# Serialize connector changes on the parent and check both the format and current default.
	frappe.get_doc("DocType", doc.doc_type, for_update=True)
	before = default_state(doc.doc_type)
	if scripts.digest(scripts.serialize(before)) != expected_default_revision:
		frappe.throw(
			"The default print format changed; read the context again.", frappe.TimestampMismatchError
		)
	from frappe.printing.doctype.print_format.print_format import make_default

	make_default(name)
	frappe.clear_cache(doctype=doc.doc_type)
	verified = default_metadata(doc.doc_type)
	if verified["default_print_format"] != name:
		frappe.throw("The default print format could not be verified.")
	audit_id = archive("Print Default", doc.doc_type, before, default_state(doc.doc_type), reason)
	return {"doc_type": doc.doc_type, **verified, "audit_id": audit_id}


def print_format_history(name, limit=20):
	get_format(name)
	return {
		"changes": frappe.get_list(
			"IGC MCP Change",
			filters={"script_type": "Print Format", "script_name": name},
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


def restore_print_format(name, expected_revision, audit_id, reason):
	doc = get_format(name, write=True)
	scripts.check_revision(doc, expected_revision)
	audit = frappe.get_doc("IGC MCP Change", audit_id)
	audit.check_permission("read")
	if (
		audit.script_type != "Print Format"
		or audit.script_name != name
		or scripts.digest(audit.before_snapshot) != audit.before_hash
	):
		frappe.throw("The backup is invalid or belongs to another print format.")
	before = json.loads(audit.before_snapshot)
	if not before:
		frappe.throw("This backup records creation; there is no earlier format to restore.")
	if before.get("doc_type") != doc.doc_type:
		frappe.throw("The backup belongs to a different document type.")
	result = save_print_format(
		name,
		doc.doc_type,
		before.get("html") or "",
		before.get("css") or "",
		reason,
		expected_revision,
		scripts.serialize({key: before[key] for key in STYLE_FIELDS if before.get(key) is not None}),
	)
	restored = frappe.get_doc("IGC MCP Change", result["audit_id"])
	restored.flags.igctools_mcp_write = True
	restored.restored_from = audit_id
	restored.save(ignore_permissions=True)
	return result


def preview_print_format(name, document_name, part="html", offset=0, length=30000):
	doc = get_format(name)
	if doc.disabled:
		frappe.throw("The print format is disabled.")
	document = frappe.get_doc(doc.doc_type, document_name)
	document.check_permission("read")
	document.check_permission("print")
	from frappe.www.printview import get_html_and_style

	result = get_html_and_style(doc=doc.doc_type, name=document_name, print_format=name)
	content = result.get(part) or ""
	return {
		"name": name,
		"document_name": document_name,
		"part": part,
		"content": content[offset : offset + length],
		"total_characters": len(content),
		"next_offset": offset + length if offset + length < len(content) else None,
		"validation": "rendered_with_frappe",
		"visual_review_required": True,
	}
