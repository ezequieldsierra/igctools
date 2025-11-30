import math
import pyclipper
import frappe
import xml.etree.ElementTree as ET

SCALE = 1000  # Clipper trabaja mejor con enteros


# ---------------------------------------------------------
# PARTE 1: utilidades para analizar el SVG y calcular pitch
# ---------------------------------------------------------

def _parse_svg_to_paths(svg_str):
    """
    Convierte un SVG sencillo de troquel en lista de paths:
    paths = [ [(x1,y1), (x2,y2), ...], ... ] en unidades del SVG.
    Soporta <polygon>, <polyline> y algo básico de <path>.
    Devuelve paths, min_y, max_y para poder escalar a mm luego.
    """
    root = ET.fromfromstring(svg_str)

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
            min_y = min(min_y, y)
            max_y = max(max_y, y)

    # polygon / polyline / path
    for el in root.iter():
        t = tag_name(el)

        # <polygon> y <polyline>
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
                except ValueError:
                    continue
            add_path(pts)

        # soporte muy simple de <path> con M/L y coords absolutas
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
            start_x = start_y = 0.0
            cmd = None

            def read_float(tok):
                try:
                    return float(tok)
                except Exception:
                    return None

            while i < len(tokens):
                tkn = tokens[i]
                if tkn.upper() in ("M", "L"):
                    cmd = tkn
                    i += 1
                    continue
                if tkn.upper() == "Z":
                    if pts and (pts[0] != pts[-1]):
                        pts.append(pts[0])
                    i += 1
                    continue

                if cmd is None:
                    i += 1
                    continue

                # coordenadas X,Y
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
                    x = nx
                    y = ny

                if cmd.upper() == "M":
                    start_x, start_y = x, y

                pts.append((x, y))

            add_path(pts)

    if min_y == float("inf"):
        min_y = 0.0
        max_y = 0.0

    return paths, min_y, max_y


def _rotate_180(paths, min_y, max_y):
    """
    Rota 180° alrededor del centro vertical del bbox.
    """
    cy = (min_y + max_y) * 0.5
    rotated = []
    for path in paths:
        new_path = []
        for x, y in path:
            nx = x
            ny = 2 * cy - y
            new_path.append((nx, ny))
        rotated.append(new_path)
    return rotated


def _paths_to_int(paths):
    out = []
    for path in paths:
        out.append(
            [(int(round(x * SCALE)), int(round(y * SCALE))) for x, y in path]
        )
    return out


def _has_overlap(paths_a, paths_b_shifted):
    pc = pyclipper.Pyclipper()
    pc.AddPaths(paths_a, pyclipper.PT_SUBJECT, True)
    pc.AddPaths(paths_b_shifted, pyclipper.PT_CLIP, True)
    sol = pc.Execute(
        pyclipper.CT_INTERSECTION,
        pyclipper.PFT_NONZERO,
        pyclipper.PFT_NONZERO,
    )
    return bool(sol)


def _min_dy_units(paths_a, paths_b):
    """
    Busca el mínimo desplazamiento dy (en unidades SVG) tal que
    A ∩ (B + dy) == ∅ usando búsqueda binaria.
    """
    paths_a_int = _paths_to_int(paths_a)
    paths_b_int_base = _paths_to_int(paths_b)

    # cota superior: ~1.5× altura del bbox
    min_y = min(y for path in (paths_a + paths_b) for _, y in path)
    max_y = max(y for path in (paths_a + paths_b) for _, y in path)
    bbox_h = max_y - min_y or 1.0

    hi = int(math.ceil(bbox_h * 1.5 * SCALE))
    lo = 0

    def shifted(dy_int):
        return [[(x, y + dy_int) for x, y in path] for path in paths_b_int_base]

    # si ya sin desplazar no hay solape, el paso mínimo es 0
    if not _has_overlap(paths_a_int, shifted(0)):
        return 0.0

    while hi - lo > 1:
        mid = (lo + hi) // 2
        if _has_overlap(paths_a_int, shifted(mid)):
            lo = mid
        else:
            hi = mid

    return hi / SCALE


@frappe.whitelist()
def compute_tetebeche_pitch(svg, height_mm, width_mm, gap_y_mm=0.0, gap_x_mm=0.0, rotation_deg=0):
    """
    API: calcula el paso Y tête-bêche mínimo en mm (usando PyClipper).

    Devuelve: { "step_y_mm": <float>, "step_x_mm": <float> }
    """
    
    # Aseguramos que los valores sean flotantes
    height_mm = float(height_mm)
    width_mm = float(width_mm)
    gap_y_mm = float(gap_y_mm)
    gap_x_mm = float(gap_x_mm)
    
    paths, min_y, max_y = _parse_svg_to_paths(svg)
    if not paths:
        # Si el parser falla, devolvemos un valor seguro (GRID)
        return {
            "step_y_mm": height_mm + gap_y_mm,
            "step_x_mm": width_mm + gap_x_mm
        }

    up_paths = paths
    down_paths = _rotate_180(paths, min_y, max_y)

    dy_units = _min_dy_units(up_paths, down_paths)

    bbox_h_units = max_y - min_y if (max_y > min_y) else 1.0
    
    if bbox_h_units == 0:
        # Prevención de división por cero
        return {
            "step_y_mm": height_mm + gap_y_mm,
            "step_x_mm": width_mm + gap_x_mm
        }

    factor_mm_per_unit = height_mm / bbox_h_units

    # Paso Y Vectorial
    step_y_mm = dy_units * factor_mm_per_unit + gap_y_mm
    
    # Paso X de Rejilla (GRID)
    step_x_mm = width_mm + gap_x_mm
    
    # Si el valor vectorial es peor o igual que GRID, usamos GRID.
    if step_y_mm >= height_mm + gap_y_mm:
        step_y_mm = height_mm + gap_y_mm

    return {"step_y_mm": step_y_mm, "step_x_mm": step_x_mm}


# ---------------------------------------------------------
# PARTE 2: papeles por material para el modo "Inventario"
# ---------------------------------------------------------

@frappe.whitelist()
def get_papeles_para_material(material: str):
    """
    Devuelve la lista de papeles disponibles para el material dado.
    """

    papeles = frappe.get_all(
        "Item",
        filters={
            "disabled": 0,
            "is_stock_item": 1,
        },
        fields=[
            "name",
            "item_name",
            "sheet_width",
            "sheet_height",
        ],
        order_by="sheet_width desc, sheet_height desc",
    )

    return papeles