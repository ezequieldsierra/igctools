import io
import re
import xml.etree.ElementTree as ET

import frappe
from frappe.utils.file_manager import save_file

try:
    import cairosvg
except ImportError:
    cairosvg = None

try:
    import ezdxf
except ImportError:
    ezdxf = None


def _tag_name(el):
    return el.tag.rsplit("}", 1)[-1].lower()


def _parse_points(points_str):
    pts = []
    if not points_str:
        return pts

    raw = points_str.replace(",", " ").split()
    nums = []
    for token in raw:
        try:
            nums.append(float(token))
        except Exception:
            continue

    for i in range(0, len(nums) - 1, 2):
        pts.append((nums[i], nums[i + 1]))

    return pts


def _normalize_svg_for_pdf(svg_str: str) -> str:
    """
    Normaliza el SVG para PDF:
    - Si width/height están en %, o no existen, los sustituye por valores
      numéricos basados en el viewBox (ancho/alto).
    """
    try:
        root = ET.fromstring(svg_str)
    except Exception:
        # Si no se puede parsear, devolvemos tal cual
        return svg_str

    vb = root.get("viewBox") or root.get("viewbox")
    if not vb:
        # Sin viewBox no inventamos nada
        return svg_str

    parts = re.split(r"[,\s]+", vb.strip())
    if len(parts) != 4:
        return svg_str

    try:
        _, _, w, h = [float(p) for p in parts]
    except Exception:
        return svg_str

    width = root.get("width", "")
    height = root.get("height", "")

    def is_percent(v: str) -> bool:
        return isinstance(v, str) and "%" in v

    # Si width/height están vacíos o en %, los sustituimos
    if not width or is_percent(width):
        root.set("width", f"{w}")
    if not height or is_percent(height):
        root.set("height", f"{h}")

    # devolvemos el SVG normalizado
    return ET.tostring(root, encoding="unicode")


def _svg_to_dxf_bytes(svg_str: str) -> bytes:
    if ezdxf is None:
        frappe.throw("La librería ezdxf no está instalada en el servidor.")

    try:
        root = ET.fromstring(svg_str)
    except Exception as e:
        frappe.throw(f"Error parseando el SVG para DXF: {e}")

    doc = ezdxf.new(setup=True)
    msp = doc.modelspace()

    for el in root.iter():
        t = _tag_name(el)

        # --- LINE ---
        if t == "line":
            try:
                x1 = float(el.get("x1", "0") or 0)
                y1 = float(el.get("y1", "0") or 0)
                x2 = float(el.get("x2", "0") or 0)
                y2 = float(el.get("y2", "0") or 0)
            except Exception:
                continue
            msp.add_line((x1, y1), (x2, y2))

        # --- POLYLINE / POLYGON ---
        elif t in ("polyline", "polygon"):
            pts = _parse_points(el.get("points", ""))
            if not pts or len(pts) < 2:
                continue
            closed = t == "polygon"
            if closed and pts[0] != pts[-1]:
                pts.append(pts[0])
            msp.add_polyline2d(pts)

        # NOTA: si luego quieres soportar <path>, aquí se añade lógica.

    # IMPORTANTE:
    # ezdxf puede escribir en modo texto o binario según el stream.
    # Usamos StringIO (texto) y luego lo convertimos a bytes.
    stream = io.StringIO()
    doc.write(stream)
    txt = stream.getvalue()
    return txt.encode("utf-8")


@frappe.whitelist()
def generar_archivo_svg_troquel(svg, nombre=None, formato="PDF"):
    if not svg:
        frappe.throw("El SVG recibido está vacío.")

    formato = (formato or "").upper()
    base = (nombre or "troquel").replace(" ", "_")

    # ------------------------ PDF ------------------------
    if formato == "PDF":
        if cairosvg is None:
            frappe.throw(
                "La librería cairosvg no está instalada en el servidor. "
                "Agrégala a requirements.txt (cairosvg) y haz bench build/restart."
            )

        # Normalizamos para evitar PDFs en blanco (width=100%, etc.)
        svg_norm = _normalize_svg_for_pdf(svg)

        try:
            pdf_bytes = cairosvg.svg2pdf(bytestring=svg_norm.encode("utf-8"))
        except Exception as e:
            frappe.throw(f"Error convirtiendo SVG a PDF: {e}")

        filename = f"{base}.pdf"
        filedoc = save_file(
            filename,
            pdf_bytes,
            "Generador de Troquel",
            nombre or "Generador",
            is_private=0,
        )
        return {"file_url": filedoc.file_url}

    # ------------------------ DXF ------------------------
    if formato == "DXF":
        try:
            dxf_bytes = _svg_to_dxf_bytes(svg)
        except Exception as e:
            frappe.throw(f"Error convirtiendo SVG a DXF: {e}")

        filename = f"{base}.dxf"
        filedoc = save_file(
            filename,
            dxf_bytes,
            "Generador de Troquel",
            nombre or "Generador",
            is_private=0,
        )
        return {"file_url": filedoc.file_url}

    # ------------------------ formato no soportado ------------------------
    frappe.throw(f"Formato no soportado: {formato}")
