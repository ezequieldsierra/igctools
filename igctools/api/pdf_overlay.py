# Convierte la página 1 de un PDF (base64) a PNG (dataURL) y devuelve el tamaño real en mm para overlay con SVG.


import base64
import re
import fitz
import frappe

@frappe.whitelist()
def pdf_page1_to_png_dataurl(pdf_b64: str, zoom: float = 2.0):
  if not pdf_b64:
    frappe.throw("PDF vacío")

  m = re.match(r"^data:application\/pdf;base64,(.+)$", pdf_b64.strip())
  b64 = m.group(1) if m else pdf_b64.strip()

  raw = base64.b64decode(b64)

  doc = fitz.open(stream=raw, filetype="pdf")
  if doc.page_count < 1:
    frappe.throw("PDF sin páginas")

  page = doc.load_page(0)
  rect = page.rect

  w_in = rect.width / 72.0
  h_in = rect.height / 72.0
  w_mm = w_in * 25.4
  h_mm = h_in * 25.4

  z = float(zoom or 2.0)
  if z < 0.5: z = 0.5
  if z > 6.0: z = 6.0

  mat = fitz.Matrix(z, z)
  pix = page.get_pixmap(matrix=mat, alpha=True)

  png_bytes = pix.tobytes("png")
  data_url = "data:image/png;base64," + base64.b64encode(png_bytes).decode("utf-8")

  doc.close()

  return {
    "data_url": data_url,
    "w_mm": w_mm,
    "h_mm": h_mm,
    "zoom": z
  }

