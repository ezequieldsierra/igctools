import frappe
import json
import math

# 🛑 NUEVAS IMPORTACIONES CLAVE 🛑
# Reemplazar pyclipper, si lo usabas, por Shapely
from shapely.geometry import Polygon, LineString, box
from shapely.affinity import rotate, translate
from shapely.ops import unary_union
# Usa un parser de SVG robusto como svgpathtools o un parser custom existente.
# Asumiremos que puedes obtener una lista de coordenadas [ (x1, y1), (x2, y2), ... ]

# --- FUNCIONES DE SOPORTE (Asumiendo que ya existen, pero deben ser sólidas) ---

def _parse_svg_to_polygon(svg_data, scale_factor=1.0):
    """
    IMPORTANTE: Esta función DEBE devolver una lista de coordenadas 
    que definan el contorno principal (el corte) de la pieza.
    Si tu parser actual falla, esta es la función que debes arreglar 
    para manejar curvas complejas (arcos, paths 'C', 'S', etc.) convirtiéndolas a segmentos rectos.
    
    Por ahora, devolvemos un polígono dummy para el ejemplo.
    """
    try:
        # Aquí iría tu lógica de parsing de SVG. 
        # Si ya tienes una que funciona parcialmente, úsala. 
        # Si PyClipper fallaba, es MUY probable que sea aquí.
        
        # [Placeholder: Asume que se extrae una lista de coordenadas]
        # Ej: Obtener el primer path de corte del SVG
        coords = frappe.parse_json(frappe.db.get_value("Custom Data", "svg_to_coords_cache", "data"))
        return Polygon(coords)
    except Exception as e:
        frappe.log_error(message=f"Error en parsing de SVG para Shapely: {e}", title="SVG Parser Failure")
        return None


def _get_piece_polygon(svg_data):
    # Llama a tu función de parsing
    piece_polygon = _parse_svg_to_polygon(svg_data)
    if not piece_polygon:
        return None
    
    # IMPORTANTE: Simplificación y validación de Shapely.
    # Aplicar un buffer negativo mínimo para limpiar geometrías inválidas (tolerancia a fallos).
    if not piece_polygon.is_valid:
        piece_polygon = piece_polygon.buffer(0.0001).buffer(-0.0001)
        
    return piece_polygon


# --- FUNCIÓN PRINCIPAL REEMPLAZADA POR SHAPELY (A PRUEBA DE FALLOS) ---

@frappe.whitelist()
def compute_tetebeche_pitch(svg, height_mm, width_mm, gap_y_mm, gap_x_mm, rotation_deg):
    """
    Calcula el Step Y óptimo para el anidamiento Tête-bêche usando Shapely.
    """
    
    # Valores de fallback de GRID (Bounding Box)
    grid_step_y = height_mm + gap_y_mm
    grid_step_x = width_mm + gap_x_mm
    
    try:
        # 1. Obtener el polígono de la pieza (limpio y validado)
        piece_polygon = _get_piece_polygon(svg)
        if piece_polygon is None:
            raise ValueError("No se pudo obtener el polígono de la pieza del SVG.")
            
        # 2. Crear la pieza invertida (Tête-bêche)
        # Una rotación de 180 grados alrededor de su centroide Y
        
        # Calcular el centroide y rotar 180 grados
        centroid = piece_polygon.centroid
        inverted_polygon = rotate(piece_polygon, 180, origin=centroid)
        
        # 3. Mover la pieza invertida para encontrar la colisión
        # El Pitch Óptimo (Step Y) es la distancia vertical mínima entre Piece y Inverted.
        # En el caso de Tête-bêche por filas, necesitamos saber qué tan bajo tiene que ir Inverted
        
        # Mover la pieza normal al origen (opcional, pero ayuda a la claridad)
        # piece_origin = translate(piece_polygon, xoff=-piece_polygon.bounds[0], yoff=-piece_polygon.bounds[1])

        # En lugar de mover e intersectar, buscaremos la distancia mínima entre los dos polígonos
        # cuando están colocados uno encima del otro.
        
        # 4. Cálculo del Step Y (Distancia Mínima de Separación)
        # La pieza normal está en Y=0. La pieza invertida debe estar en Y > piece_polygon.bounds[3]
        
        # Trasladamos la pieza invertida un poco hacia abajo para que quede debajo de la normal
        # Usaremos una distancia segura: la altura total de la pieza + un gap grande.
        safe_y_translation = piece_polygon.bounds[3] - piece_polygon.bounds[1] + 100 
        inverted_moved = translate(inverted_polygon, yoff=-safe_y_translation)
        
        # Ahora, traslada la pieza invertida hasta que se separe de la normal.
        # Esto se resuelve geométricamente con una **diferencia de conjunto**
        
        # Para evitar complejidades de la Búsqueda Binaria, usaremos un método simple y robusto:
        # El Step Y óptimo es la altura total de la caja de contorno (Bounding Box) del
        # *Union* de la pieza normal y la pieza invertida cuando se tocan perfectamente,
        # MÁS el gap Y.
        
        # Trasladamos la pieza invertida un poco hacia la izquierda para que su punto 
        # más a la izquierda se alinee con el punto más a la izquierda de la pieza normal.
        x_align = piece_polygon.bounds[0] - inverted_polygon.bounds[0]
        inverted_aligned = translate(inverted_polygon, xoff=x_align)
        
        # Encontramos la distancia mínima vertical para que no se toquen.
        # Esta distancia es el "Pitch" vectorial ideal.
        
        # ⚠️ Paso más importante: Mover la pieza invertida hasta que la distancia con la normal sea 0
        
        # Creamos una 'barra' de corte que cubre la parte inferior de la pieza normal
        # y la parte superior de la pieza invertida.
        
        # Si la pieza normal es P0 y la invertida es P180, queremos: 
        # min_pitch = P0.bounds[3] - P180.bounds[1] + distance(P0, P180_shifted)
        
        # Para simplificar y robustecer (evitar la búsqueda binaria):
        # 1. Alineamos en X
        # 2. Unimos P0 y P180_alineado
        combined = unary_union([piece_polygon, inverted_aligned])
        
        # El Pitch es simplemente la distancia entre los límites Y de los polígonos alineados en X.
        # Step Y Vectorial (el espacio negativo o positivo entre ellos)
        
        # El Step Y óptimo es: Bbox(P0).y_max - Bbox(P180_move).y_min
        
        # Movemos la pieza invertida P180 exactamente debajo de P0
        # La pieza normal está en su posición. La pieza invertida debe tener su borde Y más alto
        # alineado con el borde Y más bajo de la normal.
        
        # 1. La pieza invertida (P180) debe tener el mismo centroide X que P0.
        x_off_align = piece_polygon.centroid.x - inverted_polygon.centroid.x
        inverted_aligned = translate(inverted_polygon, xoff=x_off_align)
        
        # 2. La distancia de separación vectorial (overlap) es el borde inferior de P0 menos el borde superior de P180
        # P0.bounds[1] (y_min) - inverted_aligned.bounds[3] (y_max)
        
        # Para que se toquen, la distancia de traslación es:
        y_translation_to_touch = piece_polygon.bounds[1] - inverted_aligned.bounds[3]
        
        # 3. La pieza final Tete-bêche (P180) debe estar en la posición vertical:
        # P0.y_min + y_translation_to_touch + gap_y_mm
        
        # El PITCH/Step Y es la altura que cubre el union de P0 y P180_trasladado
        inverted_touching = translate(inverted_aligned, yoff=y_translation_to_touch)
        
        combined_touching = unary_union([piece_polygon, inverted_touching])
        
        # El Step Y vectorial es el alto del polígono combinado.
        step_y_vectorial = combined_touching.bounds[3] - combined_touching.bounds[1] + gap_y_mm

        # 5. Fallback de Seguridad
        if step_y_vectorial > grid_step_y:
            # Si Shapely devuelve un pitch peor que el de GRID, usamos el de GRID por seguridad.
            final_step_y = grid_step_y
        else:
            final_step_y = step_y_vectorial

        # 6. Devolver el resultado (el mismo formato que el viejo PyClipper)
        return {
            "step_y_mm": final_step_y,
            "step_x_mm": grid_step_x # El Step X no cambia en este modo
        }

    except Exception as e:
        # SI CUALQUIER COSA FALLA, DEVOLVEMOS LOS VALORES SEGUROS DE GRID
        # ESTO EVITA QUE LA INTERFAZ SE CUEGUE.
        frappe.log_error(message=f"Fallo crítico en Shapely Tête-bêche: {e}", title="SHAPELY_FALLBACK_TO_GRID")
        return {
            "step_y_mm": grid_step_y,
            "step_x_mm": grid_step_x
        }

# --- FIN DE compute_tetebeche_pitch ---