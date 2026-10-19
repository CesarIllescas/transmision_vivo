# Transmisión en Vivo

Aplicación de escritorio en Python y Flet para capturar video y audio y enviarlos
a servidores RTMP mediante FFmpeg.

## Instalación

Python 3.11 o superior. Las comprobaciones del refactor se ejecutaron con Python
3.13 en Windows y Flet 0.24.1. FFmpeg debe estar disponible en `PATH` o junto al
ejecutable empaquetado. La captura Blackmagic en macOS requiere una instalación
de OpenCV/GStreamer con soporte DeckLink; la rueda estándar de OpenCV puede no
incluirlo. PyAudio requiere PortAudio en los sistemas que compilan esta dependencia.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe main.py
```

En macOS, usar `.venv/bin/python` en lugar de `.venv\Scripts\python.exe`.

## Organización

- `pages/home_view.py`: controles de Flet y eventos de la interfaz. Cada vista
  posee su sesión; el montaje inicia la vista previa y el cierre libera recursos.
- `media/session.py`: captura, estado de la transmisión y coordinación de recursos.
- `media/audio.py`: micrófono y audio PCM de cortinillas mediante FIFO en macOS.
- `media/commands.py`: construcción de comandos y destinos FFmpeg sin ejecutarlos.
- `media/devices.py` y `media/frames.py`: descubrimiento de dispositivos y fotogramas.
- `classes/streamer.py`: ciclo de vida del proceso FFmpeg y lectura de diagnósticos.
- `db/db_conn.py`: almacenamiento SQLite con conexiones por operación y escrituras
  atómicas. Conserva el esquema `transmisiones` y los datos existentes.
- `pages/Router.py` y `pages/navigation.py`: navegación creada por página.
- `logger/logs.py`: logger de aplicación sin modificar el logger raíz.

Importar módulos no inicia Flet, abre dispositivos ni modifica archivos temporales.
La cola de vista previa conserva solo el fotograma más reciente. Los comandos se
ejecutan como listas de argumentos y los errores presentados al usuario no incluyen
las claves RTMP. La base de datos predeterminada se resuelve desde la raíz del
proyecto, independientemente del directorio desde el que se ejecute la aplicación.

Se conserva la interfaz `HomeView(router)` y las API anteriores de estado,
repositorio y `Streamer` para facilitar una migración gradual.

## Validación

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
```

Las pruebas usan SQLite temporal, controles reales de Flet y procesos/dispositivos
simulados. No leen las claves guardadas ni realizan transmisiones.

## Pendientes de la fase de corrección de bugs

- Validar captura y sincronización de audio/video con hardware y FFmpeg reales,
  especialmente cortinillas con tasas de fotogramas diferentes de 30 fps.
- La opción X conserva el destino provisional original. Para un endpoint
  proporcionado por el servicio, usar la opción Otro.
- Revisar la sintaxis de captura de audio AVFoundation sin cortinillas en macOS:
  se conservó el argumento original `audio=<dispositivo>`.
- Las cortinillas siguen sin estar disponibles en Windows y no se añadió soporte
  de captura para Linux.
- Cambiar el archivo de cortinilla durante una transmisión requiere detenerla;
  alternar entre la cámara y la cortinilla preparada sigue disponible en macOS.

La base de datos existente no fue modificada durante el refactor. Las pruebas
automatizadas no sustituyen la comprobación audiovisual de una transmisión real.
