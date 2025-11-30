# apps/igctools/igctools/api/troquel_export.py

import re
import math
import frappe
from vias_packdesign.api import packdesign_pdf

MM_PER_IN = 25.4


def _parse_dim_attr(raw):
    """Convierte un atributo width/height '200mm', '8.5in', '800px' → mm aprox."""
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None

    val_str = ""
    unit = ""
    for ch in s:
        if ch.isdigit() or ch in ".-":
            val_str += ch
        else:
            unit += ch

    try:
        val = float(val_str)
    except Exception:
        return None

    u = unit.strip().lower()
    if u in ("in", "inch", "inches", '"', "in."):
        return val * MM_PER_IN
    if u in ("mm", "millimeter", "millimeters", ""):
        return val
    if u in ("cm", "centimeter", "centimeters"):
        return val * 10.0
    return val


def _parse_svg_size(svg_str):
    """
    Lee viewBox o width/height del SVG y devuelve (width_mm, height_mm).

    Para tus troqueles, el viewBox ya está en mm, así que usamos width/height
    del viewBox directamente como mm.
    """
    width_mm = None
    height_mm = None
    text = svg_str or ""

    # 1) viewBox="minx miny width height"
    m = re.search(r'viewBox\s*=\s*"([^"]+)"', text, flags=re.IGNORECASE)
    if m:
        parts = m.group(1).strip().split()
        if len(parts) == 4:
            try:
                w = float(parts[2])
                h = float(parts[3])
                width_mm = w
                height_mm = h
            except Exception:
                pass

    # 2) Fallback: width="xxxmm" height="yyy..." (si no tuviera viewBox decente)
    if width_mm is None or height_mm is None:
        mw = re.search(r'width\s*=\s*"([^"]+)"', text, flags=re.IGNORECASE)
        mh = re.search(r'height\s*=\s*"([^"]+)"', text, flags=re.IGNORECASE)
        if mw:
            width_mm = _parse_dim_attr(mw.group(1)) or width_mm
        if mh:
            height_mm = _parse_dim_attr(mh.group(1)) or height_mm

    if width_mm is None:
        width_mm = 100.0
    if height_mm is None:
        height_mm = 100.0

    return float(width_mm), float(height_mm)


def _unique_filename(basename, width_mm, height_mm, ext):
    """Igual filosofía que PackDesign: Nombre_WxHmm_YYYYMMDDhhmmss.ext"""
    w = int(round(float(width_mm) if math.isfinite(width_mm) else 0))
    h = int(round(float(height_mm) if math.isfinite(height_mm) else 0))
    ts = frappe.utils.now().replace("-", "").replace(":", "").replace(" ", "")[:14]
    safe_base = re.sub(r'[\\/:*?"<>|]+', "_", str(basename or "Troquel")).strip() or "Troquel"
    return f"{safe_base}_{w}x{h}mm_{ts}.{ext}"


@frappe.whitelist()
def generar_archivo_svg_troquel(svg, nombre=None, formato="PDF"):
    """
    Wrapper del export de PackDesign para PDF / DXF.

    - svg: string del SVG (tu campo svg_troquel)
    - nombre: se usa como base para el filename
    - formato: 'PDF' o 'DXF'
    """
    svg = svg or ""
    if "<svg" not in svg:
        frappe.throw("SVG inválido (no se encontró etiqueta <svg>).")

    fmt = (formato or "PDF").strip().upper()
    if fmt not in ("PDF", "DXF"):
        frappe.throw(f"Formato no soportado: {fmt}")

    width_mm, height_mm = _parse_svg_size(svg)
    basename = (nombre or "Troquel").strip() or "Troquel"
    ext = fmt.lower()
    filename = _unique_filename(basename, width_mm, height_mm, ext)

    # Usamos EXACTAMENTE las mismas funciones que PackDesign
    if fmt == "PDF":
        res = packdesign_pdf.export_packdesign_instance_pdf(
            docname=nombre or basename,
            svg=svg,
            width_mm=width_mm,
            height_mm=height_mm,
            filename=filename,
            is_private=0,
        )
    else:  # DXF
        res = packdesign_pdf.export_packdesign_instance_dxf(
            docname=nombre or basename,
            svg=svg,
            width_mm=width_mm,
            height_mm=height_mm,
            filename=filename,
            is_private=0,
        )

    # Aseguramos estructura { "file_url": ... }
    if isinstance(res, dict) and res.get("file_url"):
        return res

    file_url = None
    if hasattr(res, "get"):
        try:
            file_url = res.get("file_url")
        except Exception:
            file_url = None

    if not file_url and isinstance(res, str):
        file_url = res

    return {"file_url": file_url}
