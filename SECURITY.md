# Seguridad

## Riesgo del flasheo

Este software escribe firmware en dispositivos hardware. Una interrupción
(cable, puerto, corte de luz, cierre forzado) puede inutilizar el dispositivo.
Úsalo bajo tu responsabilidad y con el dispositivo conectado directamente.

## Verificaciones que hace la app

- SHA-256 del binario descargado frente al publicado por el fabricante.
- Comprobación de firma/magic del firmware y de la edición del dispositivo.
- Atestación de autenticidad del dispositivo cuando el fabricante la ofrece.

Si alguna comprobación falla, la app aborta y lo registra.

## Datos

- No se envía telemetría ni información del dispositivo a terceros.
- El emparejamiento con BitBox se guarda en `~/.cache` (claves locales).
- Los registros se escriben en `~/.cache/<app>.log` y pueden contener el nombre
  del dispositivo y códigos de emparejamiento: no los publiques.

## Reportar problemas

Abre un issue describiendo versión de la app, modelo y firmware del
dispositivo, y los pasos para reproducirlo. No incluyas claves, seeds ni
códigos de emparejamiento.
