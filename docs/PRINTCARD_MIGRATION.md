# PrintCard: traslado del motor a IGCTools

## Alcance y estado

Esta fase traslada el controlador, generación de PDF, selección de Canvas, firma,
listado y filtro de permisos de PrintCard a IGCTools. Conserva los tres campos:

| Campo | Función conservada |
| --- | --- |
| `archivo` | PDF original intacto; la separación se hace en memoria |
| `printcard_file` | PDF con Canvas: separaciones por capas o páginas originales según el archivo |
| `printcard_file_signed` | PDF generado con firma y fecha en sus páginas |

**PowerPro permanece instalado por diseño.** La definición
estándar del DocType, sus dependencias y su botón nativo siguen suministrados por
PowerPro. Los Client Scripts, Server Scripts, Web Forms, campos y registros del
sitio se mantienen. No desinstalar PowerPro ni cambiar propietarios de módulos.
Los demás módulos de PowerPro están fuera de este traslado.

El motor nuevo no hereda ni importa el controlador anterior. Conserva la lógica
funcional del origen auditado. Además de imports, formato y cabeceras, incluye
ajustes concretos exigidos al revisar el código: cuatro consultas SQL usan
parámetros, las rutas PDF deben quedar dentro del directorio de archivos del
sitio, las API tienen tipos de argumentos y los textos constantes pasan por la
traducción de Frappe. Las pruebas AST permiten esos ajustes y la nueva llamada de preparación por
capas; el resto de la lógica se compara con el origen. Las rutas con
recorridos fuera del directorio y los enlaces simbólicos externos se rechazan;
los textos pueden aparecer traducidos según el idioma del usuario.

Esto conserva otras limitaciones existentes; no constituye una revisión completa
de seguridad ni una refactorización funcional. Los Canvas siguen siendo plantillas
Jinja administradas por usuarios de confianza, renderizadas en el sandbox de
Frappe. Verificar los permisos de escritura de Canvas en la configuración del sitio es una
condición de activación. Las excepciones puntuales de Semgrep documentan ese uso
intencional, el acceso a archivos ya confinado y el cambio de usuario/commit
necesario en el sitio desechable de pruebas; no se desactiva el escáner.

## Separación por capas

Para un PDF de **una página** en `archivo` con grupos de producción reconocibles,
`layers.py` prepara estas páginas antes de pasarlas al compositor Canvas existente:

| Orden | Contenido | Condición |
| --- | --- | --- |
| 1 | ARTE + TROQUEL + PRESERVADO | Siempre en el modo por capas |
| 2 | TROQUEL | Sólo si contiene elementos dibujados |
| 3 | RELIEVE | Sólo si contiene elementos dibujados |
| 4 | ESTAMPADO | Sólo si contiene elementos dibujados |
| 5 | BARNIZ BRILLO | Sólo si contiene elementos dibujados |
| 6 | BARNIZ MATTE | Sólo si contiene elementos dibujados |

Las páginas ausentes se omiten y la numeración queda consecutiva. DIMENSIONES y
los elementos sin un grupo de producción quedan fuera. Los subgrupos pertenecen
a su grupo principal. Se aceptan nombres sin distinción de mayúsculas y prefijos
como `1. TROQUEL`. Una capa sólo con estado gráfico o recorte no produce una hoja.

Soporta OCG estándar e Illustrator `/Layer`, incluidos los streams
`/AltAI8 /HiddenLayer`: oculto no significa vacío. Los recursos propios de cada
capa oculta se conservan dentro de Form XObjects para evitar colisiones de fuentes
y colores. La salida conserva trazados vectoriales, imágenes, cajas y escala;
no convierte el arte a una imagen. La firma se aplica al PDF generado completo.

Los PDFs de varias páginas o sin grupos de producción identificables conservan
el recorrido anterior. El archivo original nunca se escribe. No se reprocesan
PrintCards históricos automáticamente. Un PDF aplanado no permite recuperar capas
perdidas. Las condiciones OCG complejas (`OCMD`), capas mal cerradas y anotaciones
en un documento reconocido por capas se rechazan antes de guardar una salida
parcial. La separación es visual, no una herramienta de redacción de información:
puede conservar texto no visible para mantener sus posiciones PDF.

Se probó privadamente el único PDF proporcionado por el usuario: una página de
entrada y cinco salidas (composición, TROQUEL, RELIEVE, BARNIZ BRILLO, BARNIZ MATTE).
ESTAMPADO no existe en esa muestra. La composición coincide píxel por píxel con
el original; Canvas y firma de prueba cubren las cinco páginas. Los bytes del
original permanecen iguales. El PDF, los renderizados y los datos del cliente
no se publican en este repositorio.

## Origen y compatibilidad

- Origen: `YefriTavarez/powerpro`, commit
  `e03c488da3cd69e2bbea69e738805687f86eee90`.
- Licencia MIT y atribución conservadas en `igctools/printcard/LICENSE.powerpro`.
- `origin.json` registra SHA-256 de los seis archivos originales. El fixture ZIP
  contiene esos archivos sin modificar para comparar ambos motores.
- El fixture de esquema procede del archivo público de PowerPro
  `powerpro/preproigc/doctype/printcard/printcard.json` en el mismo commit;
  no es una exportación de metadatos privados del sitio. Los Server Scripts
  privados no se incluyen ni se publican en este repositorio.
- Frappe objetivo: 15.121.0, según el sitio auditado. El número de versión de
  PowerPro por sí solo no identifica el código instalado; debe coincidir el hash.
- No se actualizan las bibliotecas PDF de producción como parte de esta fase.
  `tests/printcard/requirements.txt` define exclusivamente un perfil aislado de
  pruebas con bibliotecas anteriores; nunca se instala dentro de un bench.
  CI conserva las dependencias declaradas por Frappe 15.121.0 y ejecuta `pip check`:
  pypdf 6.15.0, WeasyPrint 69.0 y pydyf 0.12.1. Así no se degrada el entorno de
  Frappe para conseguir que las pruebas pasen.

Los hooks sustituyen el controlador completo y siete rutas de generación, firma
y listado, incluidas las rutas antiguas que utilizan los scripts actuales. Esto
es necesario porque una sustitución HTTP no intercepta imports internos Python.
Los eventos SVG de PrintCard/Project y el override de Job Card siguen vigentes.
La integración descubrió un defecto anterior en SVG: Frappe puede devolver como
texto un PDF válido en UTF-8 y PyMuPDF requiere bytes. El lector de adjuntos de
IGCTools ahora recupera esos mismos bytes antes de renderizar. Se comprueban ambas
formas de contenido y se conserva el filtro del File adjunto al PrintCard exacto.
Las tareas de firma nuevas usan la función de IGCTools y `enqueue_after_commit`.
Las tareas antiguas conservan una ruta válida mientras PowerPro siga instalado.

El filtro de lista de IGCTools replica el de PowerPro. Frappe combina ambos con
AND si conserva los dos hooks; la condición repetida no amplía ni reduce el
conjunto permitido. No se deshabilitan en bloque scripts ni eventos de PowerPro.

## Pruebas y límites

| Contrato | Evidencia automatizada |
| --- | --- |
| Métodos originales del controlador y auxiliares | Comparación AST de los seis módulos contra el origen con hash |
| Generación y Canvas | PDF vectorial de dos páginas, Canvas horizontal y vertical |
| Capas | Orden, vacías, ocultas, OCG, subgrupos, recursos locales, Canvas y firma en cinco hojas |
| Resultado PDF | Igualdad de páginas, medidas, texto, trazados y raster de ambos motores |
| Firma | Imagen y fecha en cada página; comparación raster del resultado firmado |
| Original | Mismos bytes y misma referencia `archivo` después de generar y firmar |
| Estados y tareas | Transiciones, generación/firma una vez, tareas después del commit |
| Arte | Versiones, historial, reemplazo, campos aprobados y eliminación final |
| Acceso | Asignaciones, consulta Website User, regeneración administrativa |
| Activación | Rechazo de fuente/metadata incompatibles y rutas/controlador efectivos |
| Rutas y SQL | Conservación de rutas válidas, rechazo de traversal/symlinks externos y valores SQL separados |
| Frappe real | Suite explícita con MariaDB y metadatos representativos derivados del código público |

Ejecución aislada: `python -m pytest tests/printcard -q`.
La suite real sólo acepta un sitio desechable llamado `test_site`, con
`allow_tests` activado y sin los DocTypes que va a crear:

```sh
bench --site test_site execute igctools.printcard.integration_checks.run
```

La suite real se ejecuta al final del CI, sin envío de correos ni ejecución de
tareas externas. Usa metadatos representativos, no una copia completa del sitio.
No sustituye la comprobación de Server Scripts privados, portal/navegador, notificaciones, adjuntos reales,
todos los formatos Canvas, concurrencia y permisos específicos de producción.
Un resultado verde no permite prometer ausencia absoluta de regresiones.

## Activación controlada

**El código cambia los hooks al desplegarse; no tiene interruptor de activación.**
`before_migrate` detecta incompatibilidades, pero no revierte un despliegue ni
impide que procesos ya reiniciados carguen el código nuevo. Por eso debe validarse
primero con una prueba acotada y mantenerse mantenimiento durante el cambio.
No basta con publicar esta rama en un bench que atiende tráfico.

**No se exige una copia completa del sitio ni un backup de muchos gigas para
esta prueba.** El alcance no cambia esquemas, propietarios ni registros históricos.
Se conserva la política normal de respaldo del proveedor, sin exigir descargar
adjuntos históricos para validar el motor.

1. Registrar el commit anterior de IGCTools, los hashes instalados de PowerPro,
   las versiones PDF y los trabajos pendientes de firma. Revisar de forma sólo
   lectura el esquema y hooks efectivos.
2. Preparar una prueba privada mínima con la configuración relevante de PrintCard:
   Canvas usado, ajustes, campos y permisos/scripts relacionados. Usar únicamente
   el PDF de muestra del usuario y registros artificiales necesarios. No copiar
   todos los adjuntos ni los registros históricos. Capturar correos y tareas para
   impedir envíos reales en la prueba.
3. En ese entorno, comprobar compatibilidad y activación:

   ```sh
   bench --site PRUEBA execute igctools.printcard.migration.assert_source_compatibility
   bench --site PRUEBA migrate
   bench --site PRUEBA execute igctools.printcard.migration.status
   ```

   Deben coincidir los seis hashes; controlador IGCTools, siete rutas IGCTools y
   paquetes PDF presentes. Si no coincide el origen instalado, auditar ese código
   y adaptar la migración; no omitir el control ni modificar hashes para pasar.
4. Con la muestra, revisar composición, páginas opcionales, Canvas, previsualización,
   regeneración y firma. Verificar los scripts privados, portal y roles de los
   flujos usados. Las pruebas automatizadas ya cubren el ciclo de vida general;
   la comprobación privada se concentra en la configuración propia del sitio.
   No probar sobre un registro histórico: generar puede escribir PDFs aunque una
   llamada de consola no haga commit en la base de datos.
5. Preparar una ventana de mantenimiento de producción, completar trabajos
   pendientes o aislar sus workers y bloquear nuevas escrituras. Desplegar el
   commit validado, ejecutar `migrate`, reiniciar servicios y verificar `status`.
   Mantener mantenimiento si cualquier paso falla. No cambiar otras apps ni
   actualizar bibliotecas PDF como parte del despliegue.
6. Probar un registro controlado con la misma muestra y reabrir el sitio. Si la
   prueba reemplaza algún valor o archivo, conservar sólo esos valores/archivos
   para su reversión. Observar errores de PDF/firma, colas y permisos.

La función `status` es de lectura y requiere System Manager. No copia registros,
no migra propietarios de DocTypes y no modifica scripts ni adjuntos.

## Reversión

Mantener PowerPro instalado y conservar la revisión anterior de IGCTools. Con
el sitio en mantenimiento, terminar o apartar los trabajos de firma que apunten
a `igctools.printcard.helper` **antes** de retirar el módulo: un worker antiguo
no puede importar un módulo eliminado. Registrar cualquier trabajo apartado y
reprocesarlo una sola vez con el motor anterior tras verificar el documento.

Volver al commit anterior mediante el mecanismo normal de despliegue, limpiar
caché y reiniciar web/workers. Verificar el controlador PowerPro y sus rutas,
probar generación/firmado y reabrir. Esta fase no cambia el esquema ni el nombre
de los campos: los PDFs y registros producidos siguen usando los mismos campos.
La reversión normal de este cambio es de código. No requiere restaurar toda la
base de datos o todos los adjuntos; restaurar solamente los valores o archivos
afectados por la prueba controlada, si los hubiera.

## Condiciones pendientes antes de producción

- Comparar el origen instalado de PowerPro con los hashes auditados.
- Completar la comprobación acotada de configuración, scripts y permisos privados.
- Comprobar dependencias PDF reales y cada Canvas activo.
- Confirmar reversión de código y tratamiento de las colas de firma.
- Mantener PowerPro instalado: no se transfiere propiedad de DocTypes.
