> **Abstract (English).** Audit of the Ledger backend: static analysis
> (`ruff`, `pyflakes`, `bandit`), dependency audit (`pip-audit`), 15 unit tests
> and manual review of HID, network, crypto and error handling. Results are
> summarised in the tables below; the full report is in Spanish.

# Auditoría de `ledger-updater`

Fecha: 2026-11-11 · Versión auditada: árbol de trabajo actual · Plataforma: Linux

## Alcance

Revisión del código Python de `ledger-updater.py` (GUI Tkinter + transporte HID +
cliente de la API/HSM de Ledger), sus dependencias y los flujos de firmware, apps,
idioma y genuinidad. Queda fuera el firmware de Ledger y el HSM, que son código
de terceros y no se pueden auditar desde aquí.

## Metodología

1. Análisis estático con `ruff` (E/F/W/B/S/SIM/RUF), `pyflakes` y `bandit`.
2. Auditoría de dependencias con `pip-audit`.
3. Tests unitarios de las funciones críticas con `pytest`.
4. Revisión manual de red, criptografía, transporte HID, concurrencia, manejo de
   errores y escritura en disco.
5. Pruebas de humo de la GUI (arranque y construcción de ventanas).

## Resultados automáticos

| Herramienta | Resultado |
|---|---|
| `ruff` | `All checks passed` |
| `pyflakes` | Sin errores |
| `bandit` | 0 high, 0 medium, 11 low (`B110`, `try/except/pass` en limpieza de recursos) |
| `pip-audit` | `No known vulnerabilities found` |
| `pytest` | 15 tests, todos pasan |
| `py_compile` | OK |

Los 11 avisos bajos de Bandit son cierres de dispositivos/ventanas que ignoran
errores a propósito; no ocultan fallos de seguridad.

## Revisión manual

### Red

- El cliente HTTP solo acepta `https://manager.api.live.ledger.com/api/...`.
- El WebSocket solo acepta `wss://scriptrunner.api.live.ledger.com/update/...`.
- Los packs de idioma solo se descargan de `https://download.languages.ledger.com/...`.
- Cualquier otro host o esquema se rechaza antes de abrir la conexión.
- El modo offline bloquea las tres rutas (HTTP, WebSocket y descarga de idioma).
- TLS con verificación estándar del sistema (no hay pinning de certificado).

### Criptografía y genuinidad

- Attestation offline con `cryptography`/OpenSSL sobre secp256k1.
- Claves de host efímeras por comprobación; nonces de 8 bytes con `secrets`.
- La clave de issuer de Ledger está fijada en el código y coincide con
  `checkGenuine.py` (`0490f5c9…dad609`).
- No se firma nada fuera del handshake de attestation.
- El chequeo online delega en el HSM oficial; no se acepta una respuesta sin
  certificado válido.

### Transporte HID

- Framing verificado contra `ledgercomm` (canal `0x0101`, tag `0x05`,
  longitud big-endian y secuencia).
- Timeouts explícitos y comprobación de `SW == 0x9000` en el modo bulk: un APDU
  rechazado aborta el flasheo en lugar de continuar.
- El dispositivo se cierra en `finally` en todos los flujos principales.

### Datos y ficheros

- No se lee ni se envía la seed, el PIN, xpubs, direcciones ni saldos.
- Solo se leen versiones, certificado, lista de apps y packs de idioma.
- La única escritura es el JSON que el usuario elige en «Exportar lista…».
- No hay `eval`, `exec`, `subprocess`, `os.system` ni descarga de ejecutables.

### Concurrencia GUI

- Los workers de red/HID solo publican eventos en una cola; las actualizaciones
  de Tkinter se hacen en el hilo principal con `after`.
- Los botones se deshabilitan mientras hay una operación en curso.
- Cerrar una ventana no deja el hilo principal bloqueado; los workers terminan
  y sus eventos se descartan.

## Hallazgos corregidos durante la auditoría

1. **Parser `LIST_APPS` off-by-one**: el byte de longitud se cuenta a sí mismo;
   la última entrada se marcaba como truncada. Corregido con lectura secuencial
   y comprobación de límites. Añadido test de regresión.
2. **Dependencia `ecdsa` vulnerable** (`PYSEC-2026-1325`, timing en P-256):
   sustituida por `cryptography`/OpenSSL. `pip-audit` queda limpio.
3. **Guardas de red**: allowlist de host y modo offline en HTTP, WebSocket y
   descarga de idioma.
4. **Bulk de firmware**: ahora se comprueba el status word de cada bloque.
5. **Pack de idioma**: se descarga y valida el script antes de borrar el pack
   anterior, para no dejar el dispositivo sin idioma si falla la red.
6. **Restauración de apps**: fallos por app no abortan el resto; se registran.

## Riesgos residuales (honestos)

- **No se puede afirmar un 100% sin probar hardware en todos los modelos.**
  Probado por el usuario en Nano S Plus: detección, genuine check y permiso de
  actualización. No validado en hardware: flasheo OSU/MCU completo, instalación
  y desinstalación de apps, cambio de idioma, attestation offline, restauración
  automática de apps e idioma, exportación JSON, recuperación desde bootloader.
- **Dependencia de servidores de Ledger**: la API y el HSM pueden cambiar o
  retirar endpoints. El firmware lo sirve Ledger; no hay binarios locales.
- **Sin pinning TLS**: un compromiso de la CA del sistema podría permitir MITM.
  El firmware va firmado por Ledger y el dispositivo verifica la firma, pero la
  metadata y el flujo HSM dependen del TLS.
- **Dependencias de terceros**: `hidapi`, `websocket-client` y `cryptography`
  están pinneadas, pero siguen siendo superficie de suministro.
- **GUI**: revisada a mano y con pruebas de humo, sin tests automáticos de
  interacción (no hay framework de UI en el proyecto).
- **Solo Linux probado**. Windows/macOS no se han ejecutado.
- **El HSM es una dependencia de confianza**: si Ledger sirviera firmware
  malicioso, el dispositivo lo rechazaría por firma, pero el flujo en sí no es
  verificable de forma independiente desde este cliente.

## Plan mínimo para cerrar la validación

1. Nano S Plus: update completo, apps (instalar/actualizar/desinstalar),
   idioma, genuino offline, export JSON.
2. Repetir en Nano S, Nano X, Stax, Flex y Gen5.
3. Probar interrupción del update y recuperación desde bootloader.
4. Probar con el dispositivo bloqueado, sin apps y con la flash llena.
5. Ejecutar `ruff`, `bandit`, `pip-audit` y `pytest` en cada release.

## Reproducir

```bash
cd ~/ledger-updater
.venv/bin/ruff check ledger-updater.py
.venv/bin/bandit -q -r ledger-updater.py
.venv/bin/pip-audit
.venv/bin/pytest -q
```

## Veredicto

El código es estáticamente limpio, sin vulnerabilidades conocidas en
dependencias, sin ejecución de comandos, sin acceso a secretos y con las rutas
de red restringidas. La confianza es alta para el código revisado, pero **no es
demostrable un 100% de fiabilidad sin la matriz de pruebas de hardware**. La
herramienta está diseñada para fallar de forma segura: si algo no cuadra, no
flashea y lo registra.
