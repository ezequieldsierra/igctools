# PrintCard: traslado del motor a IGCTools

## Alcance y estado

Esta fase traslada el controlador, generación de PDF, selección de Canvas, firma,
listado y filtro de permisos de PrintCard a IGCTools. Conserva los tres campos:

| Campo | Función conservada |
| --- | --- |
| `archivo` | PDF original; no se sustituye ni se separa por capas |
| `printcard_file` | PDF generado con Canvas, una hoja por página del original |
| `printcard_file_signed` | PDF generado con firma y fecha en sus páginas |

**No representa todavía independencia completa de PowerPro.** La definición
estándar del DocType, sus dependencias y su botón nativo siguen suministrados por
PowerPro. Los Client Scripts, Server Scripts, Web Forms, campos y registros del
sitio se mantienen. No desinstalar PowerPro ni cambiar propietarios de módulos.
Los demás módulos de PowerPro están fuera de este traslado.

El motor nuevo no hereda ni importa el controlador anterior. Conserva la lógica
funcional del origen auditado. Además de imports, formato y cabeceras, incluye
ajustes concretos exigidos al revisar el código: cuatro consultas SQL usan
parámetros, las rutas PDF deben quedar dentro del directorio de archivos del
sitio, las API tienen tipos de argumentos y los textos constantes pasan por la
traducción de Frappe. Las pruebas AST permiten exclusivamente esos ajustes
documentados; el resto de la lógica se compara con el origen. Las rutas con
recorridos fuera del directorio y los enlaces simbólicos externos se rechazan;
los textos pueden aparecer traducidos según el idioma del usuario.

Esto conserva otras limitaciones existentes; no constituye una revisión completa
de seguridad ni una refactorización funcional. Los Canvas siguen siendo plantillas
Jinja administradas por usuarios de confianza, renderizadas en el sandbox de
Frappe. Verificar los permisos de escritura de Canvas en la copia privada es una
condición de activación. Las excepciones puntuales de Semgrep documentan ese uso
intencional, el acceso a archivos ya confinado y el cambio de usuario/commit
necesario en el sitio desechable de pruebas; no se desactiva el escáner.

No se activan aún las páginas por capas. Eso será un cambio separado: primera
página ARTE + TROQUEL + PRESERVADO; páginas adicionales de TROQUEL, RELIEVE,
ESTAMPADO, BARNIZ BRILLO y BARNIZ MATTE sólo cuando tengan contenido, incluidos
subgrupos. DIMENSIONES no participa. Se necesita un PDF real con grupos de capas
para validar esa fase, porque una captura de Illustrator no prueba cómo están
representados en el PDF exportado.

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
primero en una copia del sitio y mantenerse mantenimiento durante el cambio.
No basta con publicar esta rama en un bench que atiende tráfico.

1. Obtener backup verificable de base de datos, archivos públicos y privados;
   registrar commits de las apps, versiones PDF y trabajos pendientes de firma.
2. Restaurar una copia aislada con las mismas apps, orden de instalación,
   bibliotecas, scripts, permisos, Canvas y archivos. Deshabilitar correos,
   webhooks e integraciones salientes en esa copia antes de probarla.
3. Desplegar esta revisión sólo en la copia y ejecutar:

   ```sh
   bench --site COPIA execute igctools.printcard.migration.assert_source_compatibility
   bench --site COPIA migrate
   bench --site COPIA execute igctools.printcard.migration.status
   ```

   Deben coincidir los seis hashes; controlador IGCTools, siete rutas IGCTools y
   paquetes PDF presentes. Si no coincide el origen instalado, auditar ese código
   y adaptar la migración; no omitir el control ni modificar hashes para pasar.
4. Repetir en la copia: crear/editar/enviar, previsualizar, regenerar, asignar y
   retirar aprobadores, aprobar/firmar, rechazar, crear versión, reemplazar y
   borrar última versión. Comprobar portal, SVG/Project, notas y notificaciones
   con entrega capturada. Abrir adjuntos privados con cada rol real. Comparar
   todos los Canvas activos y al menos un PrintCard histórico por flujo usado.
5. Sólo con esa aceptación, preparar ventana de mantenimiento de producción,
   completar trabajos pendientes o aislar sus workers y bloquear nuevas
   escrituras. Desplegar el commit validado, ejecutar `migrate`, reiniciar
   servicios y verificar `status`. Mantener mantenimiento si cualquier paso falla.
6. Hacer las comprobaciones acordadas y reabrir el sitio; observar errores de
   firma/PDF, colas, permisos y referencias de archivos.

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
Una restauración completa sólo procede si la validación identifica corrupción;
debe reconciliar las escrituras posteriores al backup para no perder trabajo.

## Condiciones pendientes antes de producción

- Comparar el origen instalado de PowerPro con los hashes auditados.
- Ejecutar la aceptación en una copia fiel del sitio de IGCARIBE.
- Comprobar dependencias PDF reales y cada Canvas activo.
- Confirmar restauración y reversión con las colas de firma contempladas.
- Planificar por separado la transferencia de propiedad de DocTypes si se desea
  retirar PowerPro; requiere auditar también sus dependencias ajenas a PrintCard.
