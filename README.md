# Actualizador de firmware para Trezor, Ledger y BitBox

> Proyecto independiente, **no oficial**. No está afiliado, patrocinado ni
> respaldado por SatoshiLabs/Trezor, Ledger SAS ni Shift Crypto AG. Los nombres
> se usan solo para indicar compatibilidad; son marcas de sus titulares.

Tres aplicaciones nativas de escritorio para una sola tarea: actualizar el
firmware de un hardware wallet por USB, sin instalar la suite del fabricante.

| Trezor | Ledger | BitBox |
|---|---|---|
| ![](docs/screenshots/trezor.png) | ![](docs/screenshots/ledger.png) | ![](docs/screenshots/bitbox.png) |

*Capturas con un dispositivo conectado; en Trezor y Ledger los datos son de
ejemplo para mostrar la interfaz completa.*

## Por qué existe esto

El firmware de un hardware wallet es un binario firmado de unos pocos megabytes.
Para instalarlo, los fabricantes te obligan a pasar por su aplicación de
escritorio, que hace mucho más que eso:

- **Trezor Suite** es una app Electron con wallet, cuenta, portfolio y
  telemetría (se puede desactivar, pero viene activada). La actualización de
  firmware vive dentro de esa app, no como herramienta aparte.
- **Ledger Live** es software cerrado y el único camino oficial para firmware,
  apps e idioma. Añade capa de cuentas, analítica de uso y una integración que
  no siempre encaja en Linux (AppImage, permisos udev, actualizaciones
  propias).
- **BitBoxApp** es código abierto, pero sigue siendo una app completa de wallet
  que tienes que instalar y mantener para algo puntual.

Para actualizar el firmware no necesitas una wallet, ni una cuenta, ni un
portfolio, ni enviar métricas de uso. Solo necesitas hablar con el dispositivo,
descargar el binario oficial y verificar que es el que dice ser.

De ahí la idea: una ventana pequeña por fabricante, sin cuenta, sin wallet y sin
telemetría, que haga exactamente eso y muestre lo que está comprobando.

## Qué hace

**Trezor**
- Detecta el modelo, firmware, edición (Universal / Bitcoin-only), PIN,
  passphrase, idioma y estado del backup.
- Descarga el firmware oficial, verifica modelo y fingerprint, y muestra la
  ruta de actualización (incluido el salto mínimo soportado).
- Cambio de edición en ambos sentidos, avisando de que borra la wallet.
- Gestión: PIN, passphrase, crear/borrar wallet, backup/verificar, recover e
  idioma.

**Ledger**
- Detecta modelo, firmware, MCU, estado de PIN y si el manager está permitido.
- Consulta el firmware publicado para el target del dispositivo, descarga y
  muestra novedades; actualiza y reinstala apps si se pide.
- Lista apps instaladas y actualizables, permite exportarlas a JSON.
- Instala packs de idioma.
- Verificación de genuinidad online (HSM del fabricante) y offline.

**BitBox**
- Detecta BitBox02 normal o en bootloader, edición y versión.
- Verifica SHA-256 del asset, magic/edición y firma; sigue la cadena de
  versiones intermedias cuando el firmware lo exige.
- Atestación de autenticidad al conectar.
- Emparejamiento con código visible en un diálogo (no escondido en el log).
- Gestión: passphrase, backup microSD, mostrar seed, crear/restaurar wallet,
  renombrar, BIP85, cambiar contraseña y borrar dispositivo.

En las tres: registro local de lo que ocurre, sin envíos a terceros.

## Cómo funciona

La estructura es deliberadamente simple:

- **Una app por fabricante**, con su propio backend. No hay una capa común de
  flasheo: cada dispositivo tiene sus comandos, sus verificaciones y sus
  estados. Mezclarlos habría sido más frágil, no más limpio.
- **PySide6/Qt** para la interfaz. El tema, los diálogos y los componentes
  (tarjetas, chips, registro) son compartidos entre Trezor y BitBox; Ledger usa
  la misma paleta y el mismo lenguaje visual. Hay un modo Tkinter heredado con
  `--tk`, útil para comparar o depurar.
- **Trabajo en hilos**: la ventana nunca se queda bloqueada mientras se
  descarga o se escribe el firmware. Cada operación tiene timeout y deja la
  interfaz en un estado coherente si algo falla.
- **Transporte directo por USB HID**, sin Bridge ni daemon intermedio.
- **Descarga y verificación en el cliente**: SHA-256, firma/magic, modelo y
  edición. Si algo no cuadra, se aborta antes de tocar el dispositivo.
- **Logs en `~/.cache/<app>.log`**, locales. Nunca se suben a ningún sitio.

## De dónde sale cada cosa

- El código de este repositorio se escribió para este proyecto. No hay UI,
  iconos ni recursos copiados de las suites oficiales.
- La comunicación con Trezor usa `trezorlib` (LGPL-3.0) y sus mensajes
  oficiales; con BitBox, la librería `bitbox02` (Apache-2.0).
- Ledger no tiene una librería de actualización equivalente: el backend habla
  HID directamente y consulta la API y el HSM del fabricante, con
  `cryptography` para las comprobaciones.
- El firmware, las traducciones y los catálogos **no se distribuyen aquí**: se
  descargan en tiempo de ejecución desde las fuentes oficiales
  (GitHub de BitBox, datos de Trezor, API de Ledger) y se verifican antes de
  instalarse.
- Los iconos son propios (una flecha de actualización sobre un dispositivo).
  La tipografía es Inter, bajo SIL OFL 1.1 (`common/fonts/OFL.txt`).
- Las dependencias y sus licencias están en `THIRD_PARTY.md`.

## Verificación y seguridad

Cada app comprueba lo que puede comprobar antes de escribir:

- **Trezor**: el firmware se parsea con `trezorlib`, se compara el modelo y el
  fingerprint publicado para esa edición.
- **BitBox**: SHA-256 del asset frente al publicado, magic de edición y firma;
  además, atestación del dispositivo al conectar.
- **Ledger**: comprobación de firmware y de la respuesta del HSM para
  genuinidad; el detalle está en `docs/AUDIT-ledger.md`.

Lo que **no** puede hacer: verificar el firmware por su cuenta si el fabricante
no publica un hash o una firma comprobable, ni sustituir la auditoría del
código del dispositivo. Y, como cualquier herramienta de flasheo, una
interrupción puede dejar el dispositivo inservible. Se ofrece sin garantía;
léelo antes de usarlo con fondos reales (`SECURITY.md`).

## Estado y límites

- Desarrollado y probado en Linux (COSMIC). No hay soporte probado de Windows
  ni macOS.
- El flujo de BitBox se validó con una BitBox02 Bitcoin-only real (detección,
  emparejamiento, atestación, backup, renombrar, BIP85, wipe).
- Trezor y Ledger están implementados contra sus librerías y APIs oficiales.
  No todos los modelos están probados; si algo falla, el registro dice en qué
  paso.
- La app no intenta ser un gestor de wallet: no guarda seeds, no firma
  transacciones y no conoce tus xpubs.

## Uso

Requisitos: Linux, Python 3.11+ y `venv`.

```bash
cd trezor && ./run.sh      # crea el venv la primera vez
cd ledger && ./run.sh
cd bitbox && ./run.sh
```

Para instalarlo en el menú de aplicaciones:

```bash
./packaging/install.sh
./packaging/install.sh --uninstall
```

Las capturas del README se regeneran sin hardware, con datos de ejemplo:

```bash
python docs/make_screenshots.py bitbox   # o trezor / ledger
```

## Estructura

```
common/        UI compartida (tema, diálogos, jobs) y fuente Inter
trezor/        app Qt, backend y modo Tkinter
ledger/        app Qt, backend, tests y auditoría
bitbox/        app Qt y backend
icons/         iconos propios
packaging/     instalador de lanzadores
docs/          capturas y auditoría
```

## Licencia y marcas

Código bajo MIT (`LICENSE`). Dependencias y fuente en `THIRD_PARTY.md`.

Trezor® es una marca de SatoshiLabs s.r.o. Ledger® es una marca de Ledger SAS.
BitBox® es una marca de Shift Crypto AG. Este proyecto no usa sus logotipos y
no implica afiliación ni respaldo.
