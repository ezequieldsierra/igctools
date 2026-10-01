"""Enable the already configured withdrawal action after fixing native assignments."""

import hashlib

import frappe

EXPECTED = [
	[
		"Server Script",
		"IGC - PrintCard - Retiro Seguro V1",
		"ddda1c14967658b2bb74ecc63d4a39f014c03859c42104bea7836be074767595",
	],
	[
		"Server Script",
		"IGC - PrintCard - Retirar para Corregir V1",
		"b2d1b4ee025af9b1f3a0dc092b0458a6dcc0158c56721ad23545fbfa37684d0e",
	],
	[
		"Client Script",
		"IGC - PrintCard - Retirar para Corregir V1",
		"d85671a24e3e7a9e9b0cb114b0a3ef3345c223433f936cb0463918b86d572473",
	],
]


def execute():
	if not frappe.db.exists("DocType", "PrintCard"):
		return
	from frappe.model.base_document import get_controller

	if get_controller("PrintCard").__module__ != "igctools.printcard.controller":
		return
	docs = []
	for doctype, name, digest in EXPECTED:
		if not frappe.db.exists(doctype, name):
			return
		doc = frappe.get_doc(doctype, name)
		if hashlib.sha256((doc.script or "").encode()).hexdigest() != digest:
			return  # Preserve any site-specific edits.
		docs.append(doc)
	# All three components were deliberately staged inactive. Preserve a partial
	# activation or later administrator decision instead of overwriting it.
	if any(
		not doc.disabled if dt == "Server Script" else doc.enabled
		for (dt, _, _), doc in zip(EXPECTED, docs, strict=True)
	):
		return
	for (doctype, _, _), doc in zip(EXPECTED, docs, strict=True):
		if doctype == "Server Script":
			doc.disabled = 0
		else:
			doc.enabled = 1
		doc.save()
