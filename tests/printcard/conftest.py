"""Isolated contract harness; never imports or connects to a live Frappe site."""

import importlib
import sys
import types
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


class Bag(dict):
	__getattr__ = dict.get
	__setattr__ = dict.__setitem__

	def append(self, fieldname, value):
		self[fieldname].append(Bag(value))


class Document:
	def __init__(self, **values):
		self.__dict__.update(values)
		self.flags = Bag()

	def get(self, name, default=None):
		return getattr(self, name, default)

	def append(self, fieldname, value):
		getattr(self, fieldname).append(Bag(value))


class ValidationError(Exception):
	pass


def fail(message, exc=ValidationError, **kwargs):
	raise exc(message)


frappe = types.ModuleType("frappe")
frappe.__path__ = []
frappe._dict = Bag
frappe.whitelist = lambda *args, **kwargs: lambda fn: fn
frappe.throw = fail
frappe.PermissionError = PermissionError
frappe._ = lambda s: s
frappe.session = Bag(user="Administrator")
frappe.flags = Bag()
frappe.local = Bag(response=Bag())
frappe.db = MagicMock()
for name in (
	"get_doc",
	"get_all",
	"get_roles",
	"msgprint",
	"enqueue",
	"log_error",
	"respond_as_web_page",
	"get_meta",
	"get_installed_apps",
	"only_for",
):
	setattr(frappe, name, MagicMock())
utils = types.ModuleType("frappe.utils")
utils.flt = lambda v, precision=None: (
	round(float(v or 0), precision) if precision is not None else float(v or 0)
)
utils.cint = lambda v: int(v or 0)
utils.today = lambda: "2026-09-19"
utils.now = lambda: "2026-09-19 12:00:00"
utils.get_url = lambda v: "https://example.test" + v
frappe.utils = utils
sys.modules["frappe"] = frappe
sys.modules["frappe.utils"] = utils
for name in ("frappe.model", "frappe.desk", "frappe.desk.form"):
	m = types.ModuleType(name)
	m.__path__ = []
	sys.modules[name] = m
doc_module = types.ModuleType("frappe.model.document")
doc_module.Document = Document
sys.modules[doc_module.__name__] = doc_module
assign = types.ModuleType("frappe.desk.form.assign_to")
assign._add = MagicMock()
assign.remove = MagicMock()
sys.modules[assign.__name__] = assign


def legacy_modules():
	"""Execute the audited, unmodified source against the same contract harness."""
	for name in (
		"powerpro",
		"powerpro.controllers",
		"powerpro.controllers.pdf_manager",
		"powerpro.controllers.printcard",
	):
		m = types.ModuleType(name)
		m.__path__ = []
		sys.modules[name] = m
	paths = {
		"pdf_manipulator.py": "powerpro.controllers.pdf_manager.pdf_manipulator",
		"signature_helper.py": "powerpro.controllers.pdf_manager.signature_helper",
		"helper.py": "powerpro.controllers.printcard.helper",
		"controller.py": "powerpro.controllers.printcard.printcard",
		"permissions.py": "powerpro.controllers.printcard.perms",
		"client.py": "powerpro.controllers.printcard.client",
	}
	loaded = {}
	with zipfile.ZipFile(ROOT / "tests/fixtures/printcard_powerpro.zip") as archive:
		for filename, path in paths.items():
			m = types.ModuleType(path)
			sys.modules[path] = m
			exec(compile(archive.read(filename), path, "exec"), m.__dict__)
			loaded[filename] = m
			if filename == "helper.py":
				for name in ("generate_pdf_for_printcard", "sign_pdf_with_base64"):
					setattr(sys.modules["powerpro.controllers.printcard"], name, getattr(m, name))
	return loaded


LEGACY = legacy_modules()
NEW = {
	name: importlib.import_module("igctools.printcard." + target)
	for name, target in {
		"controller.py": "controller",
		"helper.py": "helper",
		"permissions.py": "permissions",
		"client.py": "client",
		"pdf_manipulator.py": "pdf_manipulator",
		"signature_helper.py": "signature_helper",
	}.items()
}


@pytest.fixture(autouse=True)
def reset_contract():
	frappe.db.reset_mock(return_value=True, side_effect=True)
	for name in (
		"get_doc",
		"get_all",
		"get_roles",
		"msgprint",
		"enqueue",
		"log_error",
		"respond_as_web_page",
		"get_meta",
		"get_installed_apps",
		"only_for",
	):
		getattr(frappe, name).reset_mock(return_value=True, side_effect=True)
	frappe.session.user = "Administrator"
	frappe.flags.clear()
	frappe.local.response = Bag()
	assign._add.reset_mock()
	assign.remove.reset_mock()
