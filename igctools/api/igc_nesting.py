import math
import pyclipper
import frappe
import xml.etree.ElementTree as ET

# Usamos una escala alta. PyClipper trabaja con enteros, por eso la multiplicación.
SCALE = 100000 
# Offset microscópico para dar área a las líneas del troquel (0.001 mm / unidad SVG)
OFFSET_AMOUNT = 1.0 # 1.0 / SCALE es 0.001 en unidades SVG originales


# ---------------------------------------------------------
# PARTE 1: utilidades para analizar el SVG y calcular pitch
# ---------------------------------------------------------

def _parse_svg_to_paths(svg_str):
    """
    Convierte un SVG sencillo de troquel en lista de paths.
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
            min_y = min(min_y, y)
            max_y = max(max_y, y)

    # polygon / polyline / path (parser básico)
    for el in root.iter():
        t = tag_name(el)

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


def _offset_paths(paths_int, offset_int):
    """Aplica el offsetting a los paths para darles área."""
    pco = pyclipper.PyclipperOffset()
    pco.AddPaths(paths_int, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    # Aplicamos el offset, y el resultado es una nueva lista de polígonos (areas)
    return pco.Execute(offset_int)


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
    A ∩ (B + dy) == ∅ usando búsqueda binaria, después de aplicar Offsetting.
    """
    # 1. Aplicar Offsetting a las paths (en enteros)
    #    paths_a_int son las líneas de la pieza A escaladas
    paths_a_int = _paths_to_int(paths_a)
    paths_b_int_base = _paths_to_int(paths_b)

    # 2. Convertir las líneas escaladas en polígonos delgados (Offset)
    #    Dividimos el OFFSET_AMOUNT por 2 para que la dilatación sea pequeña en total
    offset_int = int(round(OFFSET_AMOUNT * SCALE / 2))
    
    # 💡 Aplicamos Offset a A y a B para que tengan área
    paths_a_offset = _offset_paths(paths_a_int, offset_int)
    paths_b_offset_base = _offset_paths(paths_b_int_base, offset_int)
    
    # Si el offsetting no produce polígonos (e.g., líneas muy cortas), volvemos a GRID
    if not paths_a_offset or not paths_b_offset_base:
        # Esto indica un fallo de parsing o líneas inválidas para Offset.
        # En este caso, el Step Y debe ser GRID (lo manejaremos en la función principal).
        return float('inf') 

    # 3. Búsqueda binaria
    min_y = min(y for path in paths_a_offset + paths_b_offset_base for _, y in path)
    max_y = max(y for path in paths_a_offset + paths_b_offset_base for _, y in path)
    bbox_h = (max_y - min_y) / SCALE or 1.0

    hi = int(math.ceil(bbox_h * 1.5 * SCALE))
    lo = 0

    def shifted(dy_int):
        return [[(x, y + dy_int) for x, y in path] for path in paths_b_offset_base]

    # si ya sin desplazar no hay solape, el paso mínimo es 0
    if not _has_overlap(paths_a_offset, shifted(0)):
        return 0.0

    result_dy_int = hi # Inicializamos con el valor más alto

    while lo <= hi:
        mid = (lo + hi) // 2
        
        if _has_overlap(paths_a_offset, shifted(mid)):
            # Hay solapamiento, necesitamos desplazar más
            lo = mid + 1
        else:
            # No hay solapamiento, este es el límite superior de seguridad
            result_dy_int = mid
            hi = mid - 1
    
    # El resultado es la distancia mínima de separación *vectorial* (en unidades enteras)
    return result_dy_int / SCALE 


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
    
    # Paso X de Rejilla (GRID)
    step_x_mm = width_mm + gap_x_mm
    
    if not paths:
        # Si el parser falla, devolvemos un valor seguro (GRID)
        return {
            "step_y_mm": height_mm + gap_y_mm,
            "step_x_mm": step_x_mm
        }

    up_paths = paths
    down_paths = _rotate_180(paths, min_y, max_y)

    # Cálculo vectorial. Retorna 'inf' si falla el offsetting.
    dy_units = _min_dy_units(up_paths, down_paths)

    bbox_h_units = max_y - min_y if (max_y > min_y) else 1.0
    
    if bbox_h_units == 0:
        # Prevención de división por cero o BBox inválido
        return {
            "step_y_mm": height_mm + gap_y_mm,
            "step_x_mm": step_x_mm
        }

    # Si el cálculo vectorial falló (ej. OffsetPaths falló en _min_dy_units)
    if dy_units == float('inf'):
         step_y_mm = height_mm + gap_y_mm
    else:
        # Calcular Step Y Vectorial
        factor_mm_per_unit = height_mm / bbox_h_units
        step_y_mm = dy_units * factor_mm_per_unit + gap_y_mm
    
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