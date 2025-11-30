import math
import pyclipper
import frappe
import xml.etree.ElementTree as ET

SCALE = 10000 # Escala aumentada para mejor precisión vectorial

# ---------------------------------------------------------
# PARTE 1: utilidades para analizar el SVG y calcular pitch
# ---------------------------------------------------------

def _parse_svg_to_paths(svg_str):
    """
    Convierte un SVG en lista de paths y calcula el BBox.
    """
    root = ET.fromstring(svg_str)

    def tag_name(el):
        return el.tag.rsplit("}", 1)[-1].lower()

    paths = []
    min_x, max_x = float("inf"), float("-inf")
    min_y, max_y = float("inf"), float("-inf")

    def add_point(x, y, pts):
        nonlocal min_x, max_x, min_y, max_y
        pts.append((x, y))
        min_x = min(min_x, x); max_x = max(max_x, x)
        min_y = min(min_y, y); max_y = max(max_y, y)

    def read_float(tok):
        try:
            return float(tok)
        except Exception:
            return None

    # Iterar elementos
    for el in root.iter():
        t = tag_name(el)
        
        # Polygons y Polylines
        if t in ("polygon", "polyline"):
            pts_attr = el.get("points") or ""
            if not pts_attr.strip(): continue
            
            coords = pts_attr.replace(",", " ").split()
            pts = []
            it = iter(coords)
            for xs, ys in zip(it, it):
                x = read_float(xs); y = read_float(ys)
                if x is not None and y is not None:
                    add_point(x, y, pts)
            if pts: paths.append(pts)

        # Paths (M, L, Z) - Mejorado para solidez
        elif t == "path":
            d = el.get("d") or ""
            if not d.strip(): continue

            tokens = []
            num = ""
            for ch in d:
                if ch.upper() in "MLZ": # Solo procesamos Move, Line, Close
                    if num: tokens.append(num); num = ""
                    tokens.append(ch)
                elif ch in " ,-\t\r\n":
                    if num: tokens.append(num); num = ""
                    # Manejar el signo negativo como parte del número
                    if ch == '-': num += ch
                else:
                    num += ch
            if num: tokens.append(num)

            pts = []
            i = 0
            x = y = 0.0
            cmd = None
            
            while i < len(tokens):
                tkn = tokens[i]
                i += 1
                
                if tkn.upper() in ("M", "L"):
                    cmd = tkn
                    continue
                
                if tkn.upper() == "Z":
                    if pts and pts[0] != pts[-1]: pts.append(pts[0])
                    if pts: paths.append(pts); pts = []
                    continue

                # Coordenadas X, Y
                if i >= len(tokens): break # Prevención de bucle infinito
                
                nx = read_float(tkn)
                ny = read_float(tokens[i])
                i += 1
                if nx is None or ny is None: continue

                # Lógica de coordenadas absolutas/relativas
                if cmd == "m" or cmd == "l":
                    x += nx; y += ny
                else:
                    x = nx; y = ny

                add_point(x, y, pts)
                
            if pts: paths.append(pts) # Añadir paths restantes

    if min_y == float("inf"): min_y, max_y = 0.0, 0.0
    return paths, min_y, max_y


def _rotate_180(paths, min_y, max_y):
    """Rota 180° alrededor del centro vertical del bbox."""
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
    """Escala paths a enteros para PyClipper."""
    out = []
    for path in paths:
        out.append(
            [(int(round(x * SCALE)), int(round(y * SCALE))) for x, y in path]
        )
    return out


def _has_overlap(paths_a, paths_b_shifted):
    """Aplica un pequeño Offset para dar área antes de la intersección."""
    
    # IMPORTANTE: Aplicar Offset solo a las líneas delgadas
    OFFSET_INT = 1 # 1 unidad de Clipper (0.0001 mm)
    
    pco = pyclipper.PyclipperOffset()
    
    # JT_ROUND es para contornos y líneas complejas. ET_CLOSEDPOLYGON garantiza área.
    pco.AddPaths(paths_a, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    paths_a_offset = pco.Execute(OFFSET_INT) 

    pco = pyclipper.PyclipperOffset()
    pco.AddPaths(paths_b_shifted, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    paths_b_offset = pco.Execute(OFFSET_INT)

    # Si no hay polígonos después del offset, no puede haber solapamiento (esto es un fallo, pero lo manejamos)
    if not paths_a_offset or not paths_b_offset:
        return False
        
    pc = pyclipper.Pyclipper()
    pc.AddPaths(paths_a_offset, pyclipper.PT_SUBJECT, True)
    pc.AddPaths(paths_b_offset, pyclipper.PT_CLIP, True)
    
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
    
    # Calculamos la altura máxima del BBox en unidades PyClipper para el límite de búsqueda.
    max_y = max(y for path in (paths_a_int + paths_b_int_base) for _, y in path)
    min_y = min(y for path in (paths_a_int + paths_b_int_base) for _, y in path)
    bbox_h_int = max_y - min_y or SCALE
    
    hi = int(math.ceil(bbox_h_int * 1.5))
    lo = 0

    def shifted(dy_int):
        return [[(x, y + dy_int) for x, y in path] for path in paths_b_int_base]

    # Si ya sin desplazar no hay solape, el paso mínimo es 0
    if not _has_overlap(paths_a_int, shifted(0)):
        return 0.0

    while hi - lo > 1:
        mid = (lo + hi) // 2
        if _has_overlap(paths_a_int, shifted(mid)):
            lo = mid
        else:
            hi = mid

    # Convertir el resultado entero a unidades SVG originales
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

    # Cálculo vectorial.
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