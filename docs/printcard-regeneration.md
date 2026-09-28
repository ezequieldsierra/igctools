# Regenerar PrintCards conservando la fecha original

La fecha procede de la pareja de contenido **imagen de firma + fecha** insertada
por el firmador en el PDF existente. Esto permite reconocer fechas desplazadas
en los canvas antiguos. Si el PDF consolidó sus flujos de contenido, se reconoce
el texto junto a **FECHA / FIRMA**. Ambas fuentes deben coincidir si están presentes.
No se infiere de `creation`, `modified`, de la fecha del arte ni de los metadatos PDF.
Todas las páginas deben mostrar una misma fecha verificable. Un archivo ausente,
una página sin fecha o fechas contradictorias dejan el registro pendiente.

La firma inicial continúa usando la fecha del día de generación. Si ya existe un
PDF firmado, el firmador normal recupera su fecha original antes de generar el
archivo. Un error de lectura nunca utiliza la fecha actual como sustituto.

## Instalación

Actualizar IGCTools en Frappe Cloud y comprobar que esté disponible el método
`igctools.printcard.regeneration.regenerate_printcard_preserving_signature`.
No ejecutar la regeneración masiva con el firmador anterior.

## Revisión sin modificaciones

Solo para System Manager, con permiso de escritura en el PrintCard:

```python
frappe.call(
    "igctools.printcard.regeneration.regenerate_printcard_preserving_signature",
    printcard_id=nombre_del_printcard,
    dry_run=1,
)
```

La respuesta identifica el canvas y la fecha original. No genera archivos ni
modifica registros. Revisar los errores antes de procesar el lote.

## Regeneración

Después de instalar y validar un registro histórico, ejecutar el mismo método
con `dry_run=0`, en lotes pequeños. El llamador controla el commit de la transacción.

- Conserva los PDF anteriores en sus URL y crea una copia adicional privada de
  ambos archivos con un manifiesto: registro completo, fecha y hashes SHA-256.
- Genera nuevos PDF en nombres únicos, manteniendo el carácter público/privado
  de cada archivo anterior.
- Comprueba la fecha original en todas las páginas del nuevo PDF firmado.
- Actualiza exclusivamente `printcard_file` y, cuando corresponde,
  `printcard_file_signed`, sin alterar `modified`.
- No ejecuta `save()`, notificaciones, asignaciones, cambios de estado, aprobaciones,
  actualización 3D ni cambios de versión. Los PrintCards sin firma siguen sin firma.
- Ante un error, elimina los nuevos archivos incompletos y conserva los enlaces
  y PDF originales. El respaldo permanece para revisión.

Registrar cada respuesta y cada error. No reintentar un lote con resultado
desconocido antes de comprobar sus enlaces: un nuevo intento completo generaría
otro respaldo y otra pareja de PDF, aunque conservaría la fecha original.

Esta actualización no inicia automáticamente ningún lote durante la instalación.
