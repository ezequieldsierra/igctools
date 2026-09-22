# Foto de ID Laboral

Función instalada como personalización de Frappe v15: pestaña **Foto de ID Laboral**
en Employee, generación a partir de `face_ref`…`face_ref4`, ajustes mediante texto,
descarga y selección de versiones anteriores.

## Uso

1. Abrir un empleado activo que tenga fotos de referencia del ponche y guardar
   cualquier cambio pendiente del formulario.
2. Entrar en **Foto de ID Laboral** y pulsar **Generar foto**.
3. Para refinar el resultado, escribir por ejemplo «soltar el pelo y dar un acabado
   más glamuroso» y pulsar **Aplicar ajuste**.
4. Descargar el PNG o recuperar una imagen previa con **Usar versión**.

La imagen se guarda automáticamente en `Employee.custom_foto_id_laboral` y la
versión seleccionada en `custom_foto_id_version`. Las fotos originales del ponche,
sus vectores y el campo general `image` no se modifican. El historial se conserva
en el DocType personalizado `Foto ID Laboral Version`.

## Configuración

Reutiliza `api_key_servicio` de **Configuracion Factura de Compra**, únicamente
desde el servidor durante la llamada a OpenAI. No se exporta la clave ni se envía
al navegador. La nueva sección permite habilitar la función y seleccionar el
modelo de imágenes; el modelo de procesamiento de facturas sigue independiente.
El modelo inicial es `gpt-image-2`.

Generar, editar o restaurar requiere permiso de escritura sobre el Employee y uno
de los roles System Manager, HR Manager, HR User, Encargado Gestión Humana o
Gerente General (o Administrator). La lectura requiere permiso sobre Employee.
Los archivos generados son privados y se adjuntan al Employee. Los registros
adicionales de File que Frappe crea al usar el mismo URL en el historial no
reemplazan la comprobación del adjunto que pertenece al empleado.

## Estándar visual

- PNG de 900 × 1200 px, fondo blanco.
- Centrado y rotación calculados mediante los puntos de referencia de los ojos:
  línea de ojos en y=456 y separación entre centros de ojos de 216 px. Solo se
  aplica transformación uniforme; no se deforma el rostro.
- Base del polo azul marino **#16263F / RGB(22,38,63)**. Una máscara conectada del
  tejido azul bajo el mentón normaliza el color y limita las variaciones de luz.
  La mediana RGB resultante y la cobertura quedan registradas. Se conservan las
  sombras y la textura, por lo que cada píxel del tejido no tiene el mismo RGB.
- El prompt fijo pide el primer botón abierto, el segundo cerrado, retoque de
  ojeras, fidelidad a la persona y el mismo diseño de polo. Los ajustes personales
  se incluyen como instrucciones subordinadas a estas reglas.

El color y el encuadre tienen posprocesamiento determinista. La fidelidad facial,
el peinado, las ojeras y los botones siguen dependiendo de la generación: deben
revisarse visualmente. Una solicitud de mayor glamour no debe cambiar la geometría
facial ni el tono de piel. No se usa esta imagen generada para reconocimiento
biométrico; se conservan las referencias originales para el ponche.

## Flujo y dependencias

`Generar/editar → En cola → Generando → Ajustando → Lista`.

La llamada JSON a `/v1/images/edits` incluye las referencias locales como data
URLs. Las ediciones incluyen primero la foto actual y después las originales.
El worker usa la cola `long` (600 segundos). El navegador consulta el estado y
normaliza la imagen con los assets de face-api ya disponibles en IGCTools:

- `/assets/igctools/face/face-api.min.js`
- `/assets/igctools/face/weights/`

La generación continúa si se cierra la página. El acabado final necesita abrir
el formulario con un usuario autorizado. Si no se detecta un único rostro o
suficiente polo azul marino, se conserva la imagen intermedia y se ofrecen
**Reintentar acabado** y **Descartar esta prueba**. No se publica una imagen sin
normalizar como sustituto silencioso.

Se bloquean solicitudes duplicadas con una clave única y un bloqueo de Employee.
La imagen anterior permanece visible mientras se genera la siguiente. La
finalización actualiza únicamente los dos campos de foto de Employee, sin guardar
el documento completo ni modificar su timestamp. Se comprueba que las referencias
originales no hayan cambiado durante el trabajo.

## Instalación y mantenimiento

Archivos fuente:

- `server.py`: API Server Script `IGC - Employee - Foto ID Laboral API V1`, método
  `igc_employee_id_photo`, sin acceso de invitados.
- `client.js`: Client Script `IGC - Employee - Foto ID Laboral V1`.
- `build_install.py`: crea el instalador auditado para System Console.

```bash
python customizations/employee_id_photo/build_install.py /tmp/install_employee_id_photo.txt
```

Ejecutar el contenido con System Manager, Python y commit explícito. La instalación
usa la validación normal de Frappe, crea exclusivamente sus propios registros y
respalda las versiones previas de sus scripts. No requiere desplegar el app ni
reiniciar servicios. El JSON transporta los caracteres de markup como escapes
Unicode para conservarlos al atravesar el campo Long Text del registro de consola.

Para retirar la función, deshabilitarla en la configuración y desactivar su Client
Script y Server Script después de que terminen los trabajos. Conservar los campos,
las imágenes y el historial para evitar pérdida de datos.

## Verificación

```bash
python -m unittest discover -s customizations/employee_id_photo -p 'test_server.py' -v
node customizations/employee_id_photo/test_color.cjs
node --check customizations/employee_id_photo/client.js
```

Las pruebas cubren la codificación binaria de referencias, rechazo de rutas
externas, roles de generación, selección correcta entre adjuntos con URLs
duplicados y convergencia de tres variantes de azul al color fijo sin alterar
piel, pelo ni fondo sintéticos. La instalación valida RestrictedPython.

Ensayo real: generación y edición desde el formulario, recuperación de la versión
anterior y regreso a la corregida, guardado privado y comparación de dos empleados.
Las tres imágenes terminadas registraron 900 × 1200, línea de ojos 456, distancia
216 y mediana del polo [22,38,63].
El repositorio no incluye imágenes, datos personales de empleados ni credenciales.

API de referencia: https://developers.openai.com/api/reference/resources/images/methods/edit
