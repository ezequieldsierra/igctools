import io
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


def _svg_to_dxf_bytes(svg_str):
    if ezdxf is None:
        frappe.throw("La librería ezdxf no está instalada en el servidor.")

    doc = ezdxf.new(setup=True)
    msp = doc.modelspace()

    root = ET.fromstring(svg_str)

    for el in root.iter():
        t = _tag_name(el)

        if t == "line":
            try:
                x1 = float(el.get("x1", "0") or 0)
                y1 = float(el.get("y1", "0") or 0)
                x2 = float(el.get("x2", "0") or 0)
                y2 = float(el.get("y2", "0") or 0)
            except Exception:
                continue
            msp.add_line((x1, y1), (x2, y2))

        elif t in ("polyline", "polygon"):
            pts = _parse_points(el.get("points", ""))
            if not pts:
                continue
            closed = t == "polygon"
            if len(pts) < 2:
                continue
            if closed and pts[0] != pts[-1]:
                pts.append(pts[0])
            msp.add_polyline2d(pts)

    stream = io.BytesIO()
    doc.write(stream)
    return stream.getvalue()


@frappe.whitelist()
def generar_archivo_svg_troquel(svg, nombre=None, formato="PDF"):
    if not svg:
        frappe.throw("El SVG recibido está vacío.")

    formato = (formato or "").upper()
    base = (nombre or "troquel").replace(" ", "_")

    if formato == "PDF":
        if cairosvg is None:
            frappe.throw(
                "La librería cairosvg no está instalada en el servidor. "
                "Agrégala a requirements.txt (cairosvg) y haz bench build/restart."
            )
        pdf_bytes = cairosvg.svg2pdf(bytestring=svg.encode("utf-8"))
        filename = f"{base}.pdf"
        filedoc = save_file(
            filename,
            pdf_bytes,
            "Generador de Troquel",
            nombre or "Generador",
            is_private=0,
        )
        return {"file_url": filedoc.file_url}

    if formato == "DXF":
        dxf_bytes = _svg_to_dxf_bytes(svg)
        filename = f"{base}.dxf"
        filedoc = save_file(
            filename,
            dxf_bytes,
            "Generador de Troquel",
            nombre or "Generador",
            is_private=0,
        )
        return {"file_url": filedoc.file_url}

    frappe.throw(f"Formato no soportado: {formato}")
