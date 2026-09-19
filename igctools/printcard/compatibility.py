"""Source gate evaluated before hooks are registered; no database or app imports."""

import hashlib
import importlib.util
import json
from pathlib import Path

CONTROLLER = "igctools.printcard.controller.PrintCard"
METHOD_OVERRIDES = {
	"igcaribe.client.generate_pdf_for_printcard": "igctools.printcard.helper.generate_pdf_for_printcard",
	"powerpro.controllers.printcard.generate_pdf_for_printcard": "igctools.printcard.helper.generate_pdf_for_printcard",
	"powerpro.controllers.printcard.helper.generate_pdf_for_printcard": "igctools.printcard.helper.generate_pdf_for_printcard",
	"powerpro.controllers.printcard.sign_pdf_with_base64": "igctools.printcard.helper.sign_pdf_with_base64",
	"powerpro.controllers.printcard.helper.sign_pdf_with_base64": "igctools.printcard.helper.sign_pdf_with_base64",
	"powerpro.controllers.printcard.get_printcard_list": "igctools.printcard.client.get_printcard_list",
	"powerpro.controllers.printcard.client.get_printcard_list": "igctools.printcard.client.get_printcard_list",
}


def source_status():
	manifest = json.loads(Path(__file__).with_name("origin.json").read_text())
	try:
		spec = importlib.util.find_spec("powerpro")
	except (ImportError, ValueError):
		spec = None
	if spec is None or not spec.submodule_search_locations:
		return {"available": False, "compatible": False, "commit": manifest["commit"], "files": []}
	root = Path(next(iter(spec.submodule_search_locations)))
	files = []
	for expected in manifest["files"].values():
		path = root.parent / expected["source"]
		try:
			actual = hashlib.sha256(path.read_bytes()).hexdigest()
		except OSError:
			actual = None
		files.append(
			{
				"path": expected["source"],
				"expected_sha256": expected["sha256"],
				"actual_sha256": actual,
				"matches": actual == expected["sha256"],
			}
		)
	return {
		"available": True,
		"compatible": bool(files) and all(row["matches"] for row in files),
		"commit": manifest["commit"],
		"files": files,
	}


def runtime_hooks():
	"""Unknown sources retain all existing PowerPro controller/API/permission hooks."""
	if not source_status()["compatible"]:
		return {}, {}, {}
	return (
		{"PrintCard": CONTROLLER},
		METHOD_OVERRIDES.copy(),
		{"PrintCard": "igctools.printcard.permissions.printcard_query_conditions"},
	)
