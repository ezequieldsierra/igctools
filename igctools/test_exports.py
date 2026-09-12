"""Regression coverage for SVG geometry and PDF/DXF exports after dependency updates."""

import io
import unittest
from unittest.mock import MagicMock, patch

import ezdxf
import fitz
from lxml import etree

from igctools.api import generador_troquel_export as exports

SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm"
viewBox="0 0 100 50">
<g data-layer="cut" fill="none" stroke="#ff0000" stroke-width="0.2">
<line x1="10" y1="10" x2="90" y2="10"/>
<line x1="90" y1="10" x2="10" y2="10"/>
<path d="M 20 20 C 30 10 40 30 50 20"/>
</g></svg>"""


class TestExports(unittest.TestCase):
	def capture_export(self, method):
		file_doc = MagicMock(file_url="/private/files/test-export", file_name="test-export")
		with patch.object(exports.frappe, "get_doc", return_value=file_doc) as get_doc:
			with patch.object(exports.frappe.db, "commit"):
				method("test-only", SVG, width_mm=100, height_mm=50, filename="test-export")
		file_doc.insert.assert_called_once_with(ignore_permissions=True)
		return get_doc.call_args.args[0]["content"]

	def test_normalization_and_duplicate_removal_preserve_curves(self):
		normalized = exports.normalize_svg_root(SVG, 100, 50)
		clean = exports._dedupe_svg_geometry(normalized, 100, 50)
		root = etree.fromstring(clean.encode())
		self.assertEqual(root.get("width"), "100.0mm")
		self.assertEqual(root.get("height"), "50.0mm")
		self.assertEqual(root.get("viewBox"), "0 0 100 50")
		self.assertEqual(len(root.xpath("//*[local-name()='line']")), 1)
		self.assertEqual(root.xpath("//*[local-name()='path']")[0].get("d"), "M 20 20 C 30 10 40 30 50 20")

	def test_pdf_preserves_physical_size_color_and_geometry(self):
		payload = self.capture_export(exports.export_generador_troquel_pdf)
		with fitz.open(stream=payload, filetype="pdf") as pdf:
			self.assertEqual(len(pdf), 1)
			self.assertAlmostEqual(pdf[0].rect.width, 100 * 72 / 25.4, places=2)
			self.assertAlmostEqual(pdf[0].rect.height, 50 * 72 / 25.4, places=2)
			drawings = pdf[0].get_drawings()
			self.assertTrue(any(drawing["color"] == (1.0, 0.0, 0.0) for drawing in drawings))
			self.assertTrue(any(item[0] == "c" for drawing in drawings for item in drawing["items"]))

	def test_dxf_preserves_millimetres_and_cut_geometry(self):
		payload = self.capture_export(exports.export_generador_troquel_dxf_v2)
		doc = ezdxf.read(io.StringIO(payload.decode()))
		self.assertEqual(doc.units, ezdxf.units.MM)
		lines = list(doc.modelspace().query("LINE"))
		self.assertGreaterEqual(len(lines), 2)
		self.assertAlmostEqual((lines[0].dxf.end - lines[0].dxf.start).magnitude, 80)
		self.assertEqual(lines[0].dxf.layer, "CUT")
		self.assertEqual(lines[0].dxf.true_color, 0xFF0000)


def run():
	result = unittest.TextTestRunner(verbosity=2).run(
		unittest.defaultTestLoader.loadTestsFromTestCase(TestExports)
	)
	if not result.wasSuccessful():
		raise RuntimeError("IGCTools export regression tests failed")
