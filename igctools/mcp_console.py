"""Audited, deduplicated System Console jobs using Frappe's restricted Python/SQL."""

import io
import time

import frappe

from igctools import mcp_scripts as scripts

MAX_OUTPUT = 1000000
RUN_TYPE = "IGC MCP Console Run"


def require_console():
	scripts.require_user()
	frappe.only_for("System Manager")
	frappe.get_doc("System Console").check_permission("write")


def metadata(doc):
	return {
		"execution_id": doc.name,
		"status": doc.status,
		"language": doc.language,
		"commit_requested": bool(doc.commit_requested),
		"committed": bool(doc.committed),
		"actor": doc.actor,
		"started_at": doc.started_at,
		"finished_at": doc.finished_at,
		"duration_seconds": doc.duration_seconds,
		"output_truncated": bool(doc.output_truncated),
		"error_type": doc.error_type,
	}


def save_run(doc):
	doc.flags.igctools_mcp_write = True
	doc.save(ignore_permissions=True)


def execute_system_console(script, request_id, reason, language="Python", commit=False):
	"""Queue once per caller-provided request id. Reuse the same id when retrying."""
	require_console()
	if language not in ("Python", "SQL") or type(commit) is not bool:
		frappe.throw(frappe._("Use Python or SQL and a boolean Commit value."))
	if not script.strip() or len(script.encode()) > 1000000:
		frappe.throw(frappe._("Script must be nonempty and at most one megabyte."))
	if language == "SQL" and commit:
		frappe.throw(frappe._("SQL console uses Frappe's read-only SQL interface; Commit must be false."))
	request_hash = scripts.digest(
		scripts.serialize(
			{
				"script": script,
				"language": language,
				"commit": commit,
				"reason": reason,
			}
		)
	)
	name = "mcp-" + scripts.digest(frappe.session.user + "\n" + request_id)[:40]
	if frappe.db.exists(RUN_TYPE, name):
		doc = frappe.get_doc(RUN_TYPE, name)
		if doc.actor != frappe.session.user or doc.request_hash != request_hash:
			frappe.throw(frappe._("The request id was already used for a different execution."))
	else:
		doc = frappe.get_doc(
			{
				"doctype": RUN_TYPE,
				"name": name,
				"request_id": request_id,
				"request_hash": request_hash,
				"actor": frappe.session.user,
				"reason": reason,
				"script": script,
				"language": language,
				"commit_requested": int(commit),
				"committed": 0,
				"status": "Queued",
			}
		)
		doc.flags.igctools_mcp_write = True
		frappe.db.savepoint("igctools_console_insert")
		try:
			doc.insert(ignore_permissions=True, set_name=name)
		except frappe.DuplicateEntryError:
			frappe.db.rollback(save_point="igctools_console_insert")
			doc = frappe.get_doc(RUN_TYPE, name)
			if doc.actor != frappe.session.user or doc.request_hash != request_hash:
				frappe.throw(frappe._("The request id was already used for a different execution."))
	if doc.status == "Queued":
		frappe.enqueue(
			"igctools.mcp_console.run_execution",
			execution_id=name,
			queue="short",
			timeout=180,
			job_id=name,
			deduplicate=True,
			enqueue_after_commit=True,
		)
	return {**metadata(doc), "next_step": "Read results with read_console_output using execution_id."}


class OutputLog(list):
	def __init__(self):
		super().__init__()
		self.characters = 0
		self.truncated = False

	def append(self, value):
		value = str(value)
		remaining = MAX_OUTPUT - self.characters
		if len(value) + 1 > remaining:
			self.truncated = True
		if remaining > 0:
			item = value[: max(0, remaining - 1)]
			super().append(item)
			self.characters += len(item) + 1


def run_source(script, language):
	from frappe.utils.safe_exec import FrappePrintCollector, read_sql, safe_exec

	require_console()
	log = OutputLog()

	class ConsolePrintCollector(FrappePrintCollector):
		def _call_print(self, *objects, **kwargs):
			with io.StringIO() as output:
				print(*objects, file=output, **kwargs)
				log.append(output.getvalue().rstrip("\n"))

	old_log = getattr(frappe.local, "debug_log", None)
	frappe.local.debug_log = log
	error_type, error = "", ""
	try:
		if language == "Python":
			# Intentional System Console: require_console() checked; native sandbox disallows script commit/rollback.
			safe_exec(  # nosemgrep: frappe-codeinjection-eval
				script,
				_globals={"_print_": ConsolePrintCollector},
				restrict_commit_rollback=True,
				script_filename="System Console",
			)
		else:
			log.append(frappe.as_json(read_sql(script, as_dict=True)))
	except Exception as exc:
		error_type, error = type(exc).__name__, frappe.get_traceback()
	finally:
		frappe.local.debug_log = old_log
	return {
		"output": "\n".join(log),
		"output_truncated": log.truncated,
		"error_type": error_type,
		"error": error[:MAX_OUTPUT],
	}


def run_execution(execution_id):
	"""Worker entry point; the row lock prevents concurrent or repeated execution."""
	doc = frappe.get_doc(RUN_TYPE, execution_id, for_update=True)
	if doc.status != "Queued":
		frappe.db.rollback()
		return
	actor = doc.actor
	# Actor is from the immutable queued audit; require_console() immediately rechecks configured user and roles.
	frappe.set_user(actor)  # nosemgrep: frappe-setuser
	try:
		require_console()
	except Exception as exc:
		doc.status = "Failed"
		doc.error_type = type(exc).__name__
		doc.error = "The configured user no longer has permission to run System Console."
		doc.finished_at = frappe.utils.now_datetime()
		save_run(doc)
		# Worker transaction boundary preserves audit status; script effects commit only when explicitly requested.
		frappe.db.commit()  # nosemgrep: frappe-manual-commit
		return
	doc.status = "Running"
	doc.started_at = frappe.utils.now_datetime()
	save_run(doc)
	# Worker transaction boundary preserves audit status; script effects commit only when explicitly requested.
	frappe.db.commit()  # nosemgrep: frappe-manual-commit
	started = time.monotonic()
	result = run_source(doc.script, doc.language)
	# Only the connector closes the script transaction; scripts cannot call commit/rollback directly.
	success = not result["error_type"]
	should_commit = success and bool(doc.commit_requested)
	if not should_commit:
		frappe.db.rollback()
	doc = frappe.get_doc(RUN_TYPE, execution_id, for_update=True)
	doc.update(result)
	doc.status = "Completed" if success else "Failed"
	doc.committed = int(should_commit)
	doc.finished_at = frappe.utils.now_datetime()
	doc.duration_seconds = round(time.monotonic() - started, 3)
	save_run(doc)
	frappe.get_doc(
		{"doctype": "Console Log", "script": doc.script, "type": doc.language, "committed": doc.committed}
	).insert()
	# Successful requested changes and their completed audit are committed together.
	# For rollback runs only the execution audit and native Console Log are persisted.
	# Worker transaction boundary preserves audit status; script effects commit only when explicitly requested.
	frappe.db.commit()  # nosemgrep: frappe-manual-commit


def read_console_output(execution_id, offset=0, length=20000, part="output"):
	require_console()
	doc = frappe.get_doc(RUN_TYPE, execution_id)
	doc.check_permission("read")
	if doc.actor != frappe.session.user:
		raise frappe.PermissionError("This execution belongs to a different user.")
	content = doc.get(part) or ""
	return {
		**metadata(doc),
		"part": part,
		"content": content[offset : offset + length],
		"total_characters": len(content),
		"next_offset": offset + length if offset + length < len(content) else None,
	}
