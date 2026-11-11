# Dependencias de terceros

Este repositorio no incluye firmware, traducciones ni binarios de los
fabricantes: se descargan en tiempo de ejecución desde sus repositorios
oficiales y se verifican antes de instalarse.

## Bibliotecas de Python

| Paquete | Versión | Licencia | Uso |
|---|---|---|---|
| `trezor` | 0.20.2 | LGPL-3.0-only | Comunicación con Trezor |
| `bitbox02` | 7.0.0 | Apache-2.0 | Comunicación con BitBox |
| `hidapi` | 0.15.0 | BSD-3-Clause / GPL-3.0 | Transporte USB HID (Ledger) |
| `websocket-client` | 1.9.2 | Apache-2.0 | API de Ledger |
| `cryptography` | 50.0.2 | Apache-2.0 OR BSD-3-Clause | Criptografía (Ledger) |
| `requests` | 2.34.2 | Apache-2.0 | Descargas y API de GitHub |
| `PySide6` | 6.8.3 | LGPL-3.0-only / GPL-2.0 / GPL-3.0 | Interfaz gráfica |

Todas se instalan como dependencias externas mediante `requirements.txt`; no
se modifican ni se incluyen sus fuentes en este repositorio.

## Fuente

| Recurso | Licencia | Nota |
|---|---|---|
| Inter (variable) | SIL Open Font License 1.1 | Ver `common/fonts/OFL.txt` |

## Marcas

Trezor es una marca de SatoshiLabs s.r.o. Ledger es una marca de Ledger SAS.
BitBox es una marca de Shift Crypto AG. Se mencionan únicamente para indicar
compatibilidad. Este proyecto no está afiliado ni respaldado por ninguno de
ellos.
