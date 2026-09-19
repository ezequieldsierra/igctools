"""Compare fixtures and hashes with an independent checkout of the audited commit."""

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path


def verify(source):
	root = Path(__file__).resolve().parents[1]
	manifest = json.loads((root / "igctools/printcard/origin.json").read_text())
	with zipfile.ZipFile(root / "tests/fixtures/printcard_powerpro.zip") as archive:
		for name, row in manifest["files"].items():
			ref = manifest["commit"] + ":" + row["source"]
			raw = subprocess.check_output(["git", "-C", str(source), "show", ref])
			blob = subprocess.check_output(["git", "-C", str(source), "rev-parse", ref], text=True).strip()
			assert raw == archive.read(name), f"Fixture bytes differ from upstream: {name}"
			assert hashlib.sha256(raw).hexdigest() == row["sha256"], f"Wrong SHA-256: {name}"
			assert blob == row["git_blob_sha"], f"Wrong Git blob: {name}"
			print(f"Verified upstream bytes, SHA-256 and Git blob: {name}")


if __name__ == "__main__":
	verify(Path(sys.argv[1]))
