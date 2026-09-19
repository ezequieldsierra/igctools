"""Constrain the legacy PDF paths to the site's selected files directory."""

from pathlib import Path

import frappe


def confined_file_path(candidate: str, files_folder: str) -> str:
	root = Path(files_folder).resolve()
	if not Path(candidate).resolve().is_relative_to(root):
		frappe.throw(frappe._("PrintCard: archivo fuera del directorio permitido."), frappe.PermissionError)
	return candidate
