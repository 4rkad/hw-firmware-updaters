# Actualizador de firmware para Trezor, Ledger y BitBox

> **Proyecto independiente y no oficial.** No está afiliado, patrocinado ni
> respaldado por SatoshiLabs/Trezor, Ledger SAS ni Shift Crypto AG. Los nombres
> Trezor, Ledger y BitBox se usan únicamente para indicar compatibilidad y son
> marcas de sus respectivos titulares.

Tres aplicaciones de escritorio (PySide6) para actualizar el firmware de un
dispositivo hardware sin instalar las suites oficiales ni enviar telemetría:

- **Trezor** — detección, firmware, edición (Universal / Bitcoin-only), idioma,
  PIN, passphrase, backup, recover y wipe.
- **Ledger** — firmware, apps, idioma, exportación del listado de apps,
  verificación de genuinidad.
- **BitBox** — firmware, emparejamiento, passphrase, backup microSD, seed,
  crear/restaurar wallet, renombrar, BIP85, contraseña y wipe.

| Trezor | Ledger | BitBox |
|---|---|---|
| ![](docs/screenshots/trezor.png) | ![](docs/screenshots/ledger.png) | ![](docs/screenshots/bitbox.png) |

## Requisitos

- Linux con entorno de escritorio (probado en COSMIC).
- Python 3.11 o superior y `venv`.
- Acceso USB al dispositivo (reglas udev habituales de cada fabricante).

## Uso

Cada app es independiente y crea su propio entorno virtual la primera vez:

```bash
cd trezor && ./run.sh     # o ./run.sh --tk para la versión Tkinter
cd ledger && ./run.sh
cd bitbox && ./run.sh
```

También puedes generar los lanzadores del menú de aplicaciones:

```bash
./packaging/install.sh            # instala iconos y entradas .desktop
./packaging/install.sh --uninstall
```

## Verificación

Antes de instalar cualquier firmware la app comprueba, según el fabricante:

- **SHA-256** del binario contra el publicado por el fabricante.
- **Magic/firma** del firmware y que corresponda a la edición del dispositivo.
- **Atestación** de autenticidad del dispositivo (Trezor y BitBox).

Todo queda en el registro visible de la aplicación.

## Seguridad

Flashear firmware puede dejar el dispositivo inutilizable si se interrumpe.
No desconectes el dispositivo durante el proceso y usa cables y puertos
fiables. El software se ofrece **sin garantía**; revisa `SECURITY.md`.

## Estructura

```
common/        componentes de UI compartidos y fuente Inter
trezor/        app y backend de Trezor
ledger/        app, backend y tests de Ledger
bitbox/        app y backend de BitBox
icons/         iconos propios (sin marcas de terceros)
packaging/     instalador de lanzadores
docs/          capturas y auditoría
```

## Licencia

Código bajo licencia MIT (`LICENSE`). Las dependencias, la fuente y sus
licencias están en `THIRD_PARTY.md`. Este repositorio no distribuye firmware ni
binarios de los fabricantes: se descargan en tiempo de ejecución.

## Marcas

Trezor® es una marca de SatoshiLabs s.r.o. Ledger® es una marca de Ledger SAS.
BitBox® es una marca de Shift Crypto AG. Este proyecto no utiliza sus logotipos
y no implica afiliación ni respaldo alguno.
