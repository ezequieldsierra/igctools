# igctools/api/nesting.py  (parte superior)

import math
import frappe
import xml.etree.ElementTree as ET

from shapely.geometry import LineString
from shapely.ops import unary_union
from shapely.affinity import rotate as shp_rotate, translate as shp_translate

# Parámetros de geometría
TOOL_RADIUS_MM = 0.05   # “grosor” de cuchilla en mm (ajustable)
CLEAR_TOL_MM   = 0.01   # tolerancia para considerar “sin solape”


# ---------------------------------------------------------
# 1) Parsear SVG → paths en unidades del SVG
# ---------------------------------------------------------

def _parse_svg_to_paths(svg_str):
    """
    Devuelve:
        paths = [ [(x1,y1), (x2,y2), ...], ... ]
        min_y, max_y   (en unidades tal cual del SVG)
    Soporta <polygon>, <polyline> y <path> simple (M/L/Z).
    """
    root = ET.fromstring(svg_str)

    def tag_name(el):
        return el.tag.rsplit("}", 1)[-1].lower()

    paths = []
    min_y = float("inf")
    max_y = float("-inf")

    def add_path(pts):
        nonlocal min_y, max_y
        if len(pts) < 2:
            return
        paths.append(pts)
        for _, y in pts:
            if y < min_y:
                min_y = y
            if y > max_y:
                max_y = y

    for el in root.iter():
        t = tag_name(el)

        # --- polygon / polyline ---
        if t in ("polygon", "polyline"):
            pts_attr = el.get("points") or ""
            if not pts_attr.strip():
                continue
            coords = pts_attr.replace(",", " ").split()
            pts = []
            it = iter(coords)
            for xs, ys in zip(it, it):
                try:
                    x = float(xs)
                    y = float(ys)
                    pts.append((x, y))
                except Exception:
                    continue
            add_path(pts)

        # --- path (M/L/Z muy simple, absolutas) ---
        elif t == "path":
            d = el.get("d") or ""
            if not d.strip():
                continue

            tokens = []
            num = ""
            for ch in d:
                if ch.upper() in "MLZHV":
                    if num:
                        tokens.append(num)
                        num = ""
                    tokens.append(ch)
                elif ch in " ,\t\r\n":
                    if num:
                        tokens.append(num)
                        num = ""
                else:
                    num += ch
            if num:
                tokens.append(num)

            pts = []
            i = 0
            x = y = 0.0
            cmd = None

            def read_float(tok):
                try:
                    return float(tok)
                except Exception:
                    return None

            while i < len(tokens):
                tk = tokens[i]

                if tk.upper() in ("M", "L"):
                    cmd = tk
                    i += 1
                    continue

                if tk.upper() == "Z":
                    # cerrar si hace falta
                    if pts and pts[0] != pts[-1]:
                        pts.append(pts[0])
                    i += 1
                    continue

                if cmd is None:
                    i += 1
                    continue

                if i + 1 >= len(tokens):
                    break

                nx = read_float(tokens[i])
                ny = read_float(tokens[i + 1])
                i += 2
                if nx is None or ny is None:
                    continue

                if cmd == "m":
                    x += nx
                    y += ny
                elif cmd == "l":
                    x += nx
                    y += ny
                else:
                    # M / L absolutas
                    x = nx
                    y = ny

                pts.append((x, y))

            add_path(pts)

    if min_y == float("inf"):
        min_y = 0.0
        max_y = 0.0

    return paths, min_y, max_y


# ---------------------------------------------------------
# 2) Paths SVG → sólido Shapely en mm
# ---------------------------------------------------------

def _svg_to_solid_mm(svg_str, height_mm):
    """
    Convierte el SVG a una geometría Shapely en mm.
    - Escala vertical para que la altura del bbox sea height_mm.
    - Aplica un buffer TOOL_RADIUS_MM a las líneas (cuchilla).
    """
    paths, min_y, max_y = _parse_svg_to_paths(svg_str)
    if not paths:
        frappe.throw("No se pudieron extraer paths del SVG para nesting.")

    bbox_h_units = max_y - min_y
    if bbox_h_units <= 0:
        bbox_h_units = 1.0

    # factor unidadesSVG → mm (en vertical)
    factor = float(height_mm) / float(bbox_h_units)

    lines = []
    min_x = float("inf")
    for pts in paths:
        pts_mm = []
        for x, y in pts:
            xx = x * factor
            yy = (y - min_y) * factor  # bottom = 0 mm
            pts_mm.append((xx, yy))
            if xx < min_x:
                min_x = xx
        if len(pts_mm) >= 2:
            lines.append(LineString(pts_mm))

    if not lines:
        frappe.throw("No se pudieron construir líneas del SVG.")

    # Normalizar X para que arranque en 0 (no afecta al stepY)
    shift_x = -min_x if min_x not in (float("inf"), float("-inf")) else 0.0
    if shift_x:
        lines = [shp_translate(ls, xoff=shift_x, yoff=0.0) for ls in lines]

    # Buffer de cuchilla y unión
    buffered = [ls.buffer(TOOL_RADIUS_MM, join_style=2) for ls in lines]
    solid = unary_union(buffered)

    return solid


# ---------------------------------------------------------
# 3) Buscar stepY mínimo tête-bêche con Shapely
# ---------------------------------------------------------

def _min_step_y_tetebeche_mm(svg_str, height_mm, gap_y_mm):
    """
    Calcula el stepY mínimo (en mm) para patrón tête-bêche 180°,
    de modo que no haya solape de sólidos (buffer de cuchilla).
    """
    solid_up = _svg_to_solid_mm(svg_str, height_mm)

    # Normalizar a bbox con minY=0
    minx, miny, maxx, maxy = solid_up.bounds
    solid_up = shp_translate(solid_up, xoff=-minx, yoff=-miny)
    minx, miny, maxx, maxy = solid_up.bounds
    h = maxy - miny

    if h <= 0:
        # fallback: altura nominal + gap
        return float(height_mm) + float(gap_y_mm)

    cx = (minx + maxx) * 0.5
    cy = (miny + maxy) * 0.5

    # Sólido invertido 180° sobre el centro
    solid_down = shp_rotate(solid_up, 180.0, origin=(cx, cy), use_radians=False)

    # Si ya no se solapan con dy=0 (muy raro), devolvemos altura + gap
    if solid_up.buffer(-CLEAR_TOL_MM).disjoint(
        solid_down.buffer(-CLEAR_TOL_MM)
    ):
        return float(h) + float(gap_y_mm)

    lo = 0.0
    hi = h  # cota máxima: una altura completa entre centros

    # Búsqueda binaria en dy
    for _ in range(40):  # precisión sub-micrón en la práctica
        mid = (lo + hi) * 0.5
        test = shp_translate(solid_down, xoff=0.0, yoff=mid)
        # Checamos intersección con un pequeño "shrink" para evitar ruido numérico
        if solid_up.buffer(-CLEAR_TOL_MM).intersects(
            test.buffer(-CLEAR_TOL_MM)
        ):
            lo = mid
        else:
            hi = mid

    # hi ≈ mínimo dy sin solape; le sumamos gap_y_mm usuario
    return hi + float(gap_y_mm)


# ---------------------------------------------------------
# 4) API pública que usa el client script
# ---------------------------------------------------------

@frappe.whitelist()
def compute_tetebeche_pitch(svg, height_mm, gap_y_mm=0.0, rotation_deg=0):
    """
    Devuelve el pitch vertical (stepY) tête-bêche en mm, usando Shapely.
    Firmas compatibes con el client script actual.
    """
    try:
        height_mm = float(height_mm or 0.0)
        gap_y_mm = float(gap_y_mm or 0.0)
    except Exception:
        frappe.throw("height_mm y gap_y_mm deben ser numéricos.")

    if height_mm <= 0:
        frappe.throw("height_mm debe ser > 0")

    step_y = _min_step_y_tetebeche_mm(svg, height_mm, gap_y_mm)
    return {"step_y_mm": step_y}
