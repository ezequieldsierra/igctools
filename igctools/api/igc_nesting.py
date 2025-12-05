import math
import frappe
import xml.etree.ElementTree as ET
from shapely.geometry import Polygon
from shapely.affinity import rotate, translate
from shapely.ops import unary_union
import pyclipper # Mantenemos esto solo por si lo necesitas para otras utilidades, aunque no lo usaremos para el cálculo principal.

# Usamos una escala grande para mantener la precisión en los floats de Shapely.
SCALE = 100000 


# ---------------------------------------------------------
# PARTE 1: utilidades para analizar el SVG y calcular pitch
# ---------------------------------------------------------

def _parse_svg_to_paths(svg_str):
    """
    Convierte un SVG en lista de paths (coordenadas flotantes).
    Solo extrae polígonos y polilíneas simples que formarán el contorno.
    """
    root = ET.fromstring(svg_str)

    def tag_name(el):
        return el.tag.rsplit("}", 1)[-1].lower()

    paths = []
    min_y = float("inf")
    max_y = float("-inf")

    # Función auxiliar para añadir puntos y actualizar el Bounding Box (BBox)
    def add_point(x, y, pts):
        nonlocal min_y, max_y
        pts.append((x, y))
        min_y = min(min_y, y)
        max_y = max(max_y, y)

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

        # Paths (M, L, Z) - Versión robusta para extraer solo coordenadas
        elif t == "path":
            d = el.get("d") or ""
            if not d.strip(): continue

            # Simplificamos el parsing para extraer solo M, L, Z
            tokens = []
            num = ""
            for ch in d:
                if ch.upper() in "MLZ":
                    if num: tokens.append(num); num = ""
                    tokens.append(ch)
                elif ch in " ,-\t\r\n":
                    if num: tokens.append(num); num = ""
                    if ch == '-': num += ch
                else: num += ch
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

                if i >= len(tokens): break
                
                nx = read_float(tkn)
                ny = read_float(tokens[i])
                i += 1
                if nx is None or ny is None: continue

                if cmd == "m" or cmd == "l": x += nx; y += ny
                else: x = nx; y = ny

                add_point(x, y, pts)
                
            if pts: paths.append(pts)

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


def _calculate_optimal_tetebeche_step(paths_normal, paths_inverted, gap_y_mm):
    """
    Calcula el Step Y mínimo para Tête-bêche usando Shapely.
    
    La estrategia es: Unir la Pieza Normal y la Pieza Invertida cuando están
    perfectamente alineadas en X, y usar la altura del polígono resultante (Union).
    Esto da el pitch mínimo garantizado.
    """
    
    # 1. Convertir todas las paths a polígonos Shapely válidos
    polygons_normal = []
    polygons_inverted = []
    
    for path in paths_normal:
        try:
            poly = Polygon(path)
            # Validar y limpiar geometrías inválidas (tolerancia a fallos)
            if poly.is_valid:
                polygons_normal.append(poly)
        except Exception:
            continue

    for path in paths_inverted:
        try:
            poly = Polygon(path)
            if poly.is_valid:
                polygons_inverted.append(poly)
        except Exception:
            continue

    if not polygons_normal or not polygons_inverted:
        # Falla al convertir las líneas a polígonos cerrados (SVG no tiene áreas cerradas)
        raise ValueError("El SVG no contiene contornos válidos para el análisis Shapely.")

    # 2. Unión de todos los polígonos de cada pieza
    piece_normal = unary_union(polygons_normal)
    piece_inverted = unary_union(polygons_inverted)
    
    # 3. Alinear la pieza invertida horizontalmente (X) con la normal
    #    para que los contornos se encajen perfectamente.
    x_offset_align = piece_normal.centroid.x - piece_inverted.centroid.x
    inverted_aligned_x = translate(piece_inverted, xoff=x_offset_align)
    
    # 4. Encontrar la distancia vertical (el Pitch)
    #    La altura del Pitch es la Y_max de la pieza normal - la Y_min de la pieza invertida
    #    cuando están unidas y encajadas.
    
    # Vamos a usar la Suma de la Unión, que nos da la figura más grande posible
    # cuando están perfectamente apiladas para el anidamiento.
    combined_union = unary_union([piece_normal, inverted_aligned_x])
    
    # El pitch óptimo es la altura total de la unión
    step_y_vectorial_pitch = combined_union.bounds[3] - combined_union.bounds[1]

    # La distancia final Step Y es el Pitch + Gap
    final_step_y = step_y_vectorial_pitch + gap_y_mm
    
    return final_step_y


@frappe.whitelist()
def compute_tetebeche_pitch(svg, height_mm, width_mm, gap_y_mm=0.0, gap_x_mm=0.0, rotation_deg=0):
    """
    API: calcula el paso Y tête-bêche mínimo en mm (usando Shapely/GEOS).

    Devuelve: { "step_y_mm": <float>, "step_x_mm": <float> }
    """
    
    height_mm = float(height_mm)
    width_mm = float(width_mm)
    gap_y_mm = float(gap_y_mm)
    gap_x_mm = float(gap_x_mm)
    
    # Valores de Respaldo (GRID)
    grid_step_y = height_mm + gap_y_mm
    grid_step_x = width_mm + gap_x_mm
    
    try:
        paths, min_y, max_y = _parse_svg_to_paths(svg)
        
        if not paths:
            frappe.throw("No se pudieron extraer contornos válidos del SVG.")

        up_paths = paths
        down_paths = _rotate_180(paths, min_y, max_y)
        
        # 💡 Cálculo del Step Y con Shapely (Lógica de anidamiento)
        step_y_mm_calc = _calculate_optimal_tetebeche_step(up_paths, down_paths, gap_y_mm)

        # 4. Lógica de Respaldo y Verificación
        if step_y_mm_calc >= grid_step_y:
            # Si el cálculo vectorial es peor o igual que GRID, usamos GRID.
            final_step_y = grid_step_y
        else:
            final_step_y = step_y_mm_calc
            
        return {"step_y_mm": final_step_y, "step_x_mm": grid_step_x}

    except Exception as e:
        # Fallback a GRID ante cualquier error de librería (PyClipper o Shapely)
        frappe.log_error(message=f"Fallo estructural en Nesting: {e}", title="GEOMETRY_FALLBACK_TO_GRID")
        return {
            "step_y_mm": grid_step_y,
            "step_x_mm": grid_step_x
        }


# ---------------------------------------------------------
# PARTE 2: papeles por material para el modo "Inventario"
# ---------------------------------------------------------

@frappe.whitelist()
def get_papeles_para_material(material: str | None = None):
    """
    Devuelve la lista de papeles disponibles cuyo reference_name
    coincide con el valor del campo 'material' en Generador de Troquel.
    """
    material = (material or "").strip()
    if not material:
        # Si no hay material, no devolvemos nada (para que el JS entre en modo Personalizado)
        return []

    papeles = frappe.get_all(
        "Item",
        filters={
            "disabled": 0,
            "is_stock_item": 1,
            "reference_name": material,   # <<--- AQUÍ ESTÁ EL FILTRO CLAVE
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
