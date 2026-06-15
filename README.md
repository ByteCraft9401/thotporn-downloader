# THOTP Downloader

Downloader para Windows que permite descargar fotos y videos desde THOTP mediante una interfaz gráfica sencilla.

El programa está diseñado para facilitar la descarga de contenido mediante:

* Interfaz gráfica (GUI).
* Descargas individuales.
* Descargas por páginas.
* Descargas de perfiles completos.
* Gestión de progreso.
* Pausa, reanudación y cancelación de tareas.
* Sistema de licencias FREE/PREMIUM.

---

# Características

## Fotos

Permite descargar:

* Fotos individuales.
* Todas las fotos de una página.
* Varias páginas seleccionadas.
* Perfiles completos.

## Videos

Permite descargar:

* Videos individuales.
* Videos por páginas.
* Videos disponibles según el tipo de licencia.

---

# Instalación

Consulta la guía completa:

```text
docs/instalación.md
```

La versión final para usuarios se distribuye como ejecutable de Windows (`.exe`).

No es necesario instalar Python para utilizar la versión compilada.

---

# Requisitos

## Sistema operativo

* Windows 10
* Windows 11

## Dependencias externas

Para descargas de video se utiliza:

* N_m3u8DL-RE

Para procesamiento multimedia puede ser necesario:

* FFmpeg

Estas herramientas se incluyen o se indican según la versión distribuida.

---

# Uso

## Versión GUI

La aplicación permite:

* Introducir la URL.
* Elegir carpeta de descarga.
* Seleccionar páginas específicas.
* Ver progreso de descarga.
* Pausar o cancelar tareas.
* Abrir directamente la carpeta de descargas.

---

# Descargas disponibles

## Foto individual

Ejemplo:

```text
https://thotporn.tv/.../photo/ID
```

## Video individual

Ejemplo:

```text
https://thotporn.tv/.../video/ID
```

## Página completa

Permite descargar todas las fotos o videos disponibles dentro de una página.

## Perfil completo

Permite procesar todo el contenido disponible de un perfil.

---

# Capturas

Las capturas de la interfaz se añadirán próximamente.

---

# Actualizaciones

El programa incluye sistema de actualización para mantener la aplicación al día cuando existan nuevas versiones.

---

# Licencias

THOTP Downloader utiliza un sistema de licencias para gestionar las funciones FREE y PREMIUM.

La información del servidor de licencias y herramientas administrativas no forma parte de este repositorio público.

---

# Desarrollo

Este repositorio contiene el cliente del programa.

Incluye:

* Código fuente.
* Configuración de compilación.
* Pruebas automatizadas.
* Documentación técnica básica.

Para ejecutar desde código fuente:

```bash
pip install -r requirements.txt
```

Ejecutar GUI:

```bash
python gui.py
```

Ejecutar pruebas:

```bash
pytest -q
```

---

# Estructura del proyecto

```text
THOTP-Downloader/

├── gui.py
├── thotp_downloader.py

├── requirements.txt
├── requirements-dev.txt

├── tests/
│   ├── test_downloader_pure.py
│   ├── test_gui_state.py
│   └── otros tests

├── docs/
│   ├── instalación.md
│   └── RELEASE_PREPARATION.md

├── build.bat
├── thotp_downloader.spec
└── thotp_downloader_gui.spec
```

---

# Compilación

El proyecto incluye configuración para generar ejecutables de Windows mediante PyInstaller.

Ejemplo:

```powershell
.\build.bat gui
```

El resultado se genera en:

```text
dist/
```

---

# Tecnologías utilizadas

Proyecto desarrollado con:

* Python
* Tkinter
* PyInstaller
* N_m3u8DL-RE
* FFmpeg

---

# Licencia

Consulta el archivo:

```text
LICENSE
```

para conocer los términos de uso y distribución.

---

# Créditos

Gracias a los proyectos de código abierto utilizados como parte del funcionamiento de la aplicación.
