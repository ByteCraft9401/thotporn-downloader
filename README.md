# THOTP Downloader

Downloader CLI para fotos y videos, con modo FREE/PREMIUM controlado por un backend local Flask y un archivo `license.txt`.

## Requisitos

- Python 3.10 o superior
- `N_m3u8DL-RE.exe` para descargar videos
- Dependencias Python listadas en `requirements.txt`

Instalacion:

```powershell
pip install -r requirements.txt
```

## Estructura Relevante

```text
thotp_downloader.py        Script principal
gui.py                     GUI Windows con Tkinter
license.txt                Licencia usada por el cliente
N_m3u8DL-RE.exe            Ejecutable para videos
backend/app.py             Backend Flask de licencias
backend/create_license.py  Generador de licencias
backend/licenses.json      Base de licencias
logs/errors.txt            Log de errores importantes
requirements.txt           Dependencias Python
```

## Arquitectura

El proyecto mantiene una separacion simple:

- `thotp_downloader.py` contiene la CLI, configuracion, validacion de licencia y flujo de descarga.
- `gui.py` contiene una interfaz grafica Tkinter separada que reutiliza funciones del downloader.
- `backend/app.py` expone el endpoint Flask `/verify`.
- `backend/create_license.py` genera licencias nuevas en `backend/licenses.json`.
- `logs/errors.txt` concentra errores importantes para diagnostico.

La configuracion principal del downloader esta centralizada al inicio del script. Los nombres historicos de constantes se mantienen para preservar compatibilidad con la logica actual.

## Backend De Licencias

El sistema de licencias usa Flask y `backend/licenses.json`.

Para iniciar el backend:

```powershell
python backend/app.py
```

El cliente valida contra:

```text
http://127.0.0.1:5000/verify
```

Panel admin web:

```text
http://127.0.0.1:5000/admin
```

El panel admin permite generar licencias, revocarlas, editar `expires_at`,
listar licencias activas y buscar por HWID. No usa base de datos; trabaja
directamente sobre `backend/licenses.json`.

El panel admin esta protegido por login con sesiones Flask. Configura el
usuario y el hash del password antes de iniciar el backend:

```powershell
python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('CAMBIA_ESTE_PASSWORD'))"
```

Usa el hash generado:

```powershell
$env:THOTP_ADMIN_USERNAME="admin"
$env:THOTP_ADMIN_PASSWORD_HASH="pega_aqui_el_hash_generado"
$env:THOTP_ADMIN_SECRET_KEY="clave_larga_aleatoria_para_sesiones"
python backend/app.py
```

Luego abre:

```text
http://127.0.0.1:5000/login
```

Si no configuras `THOTP_ADMIN_PASSWORD_HASH`, el login admin queda bloqueado.
El endpoint `/verify` no requiere sesion admin.

## Crear Y Usar Una Licencia

Generar una licencia:

```powershell
python backend/create_license.py
```

Copia la clave generada en `license.txt`. El archivo debe contener solo la clave, por ejemplo:

```text
THOTP-ABCD1234
```

La primera PC que valida una licencia queda vinculada por HWID. Si no hay licencia valida o el backend no esta disponible, el script funciona en modo FREE.

Regla anti-sharing: cuando `hwid` esta en `null`, el backend guarda automaticamente el primer HWID recibido y permite la activacion. Desde ese momento, la licencia solo funciona con ese mismo HWID; cualquier otro dispositivo recibe una respuesta invalida para ese dispositivo.

Formato actual de `backend/licenses.json`:

```json
{
  "THOTP-ABCD1234": {
    "premium": true,
    "expires_at": "2026-06-18T12:00:00",
    "disabled": false,
    "hwid": null
  }
}
```

El backend valida que la licencia exista, sea premium, no este deshabilitada, no este expirada y corresponda al mismo HWID. Las licencias antiguas con el campo `expires` siguen siendo aceptadas y se migran a `expires_at` cuando se vinculan a una PC.

## Auto-Update Del Downloader

El downloader tiene una version local centralizada en `AppConfig` y consulta:

```text
http://127.0.0.1:5000/version.json
```

El backend publica ese manifest desde variables de entorno:

```powershell
$env:THOTP_LATEST_VERSION="1.0.1"
$env:THOTP_DOWNLOAD_URL="https://tu-servidor/thotp_downloader.exe"
$env:THOTP_DOWNLOAD_SHA256="sha256_del_exe"
$env:THOTP_UPDATE_NOTES="Notas breves de la version"
python backend/app.py
```

Para calcular el SHA256 del `.exe`:

```powershell
Get-FileHash .\dist\thotp_downloader.exe -Algorithm SHA256
```

En una ejecucion normal, el downloader intenta consultar `version.json`. Si no
hay backend o internet, registra el fallo y continua funcionando. Si detecta una
version nueva, muestra un aviso sin interrumpir FREE/PREMIUM ni las descargas.

Para descargar y preparar la actualizacion:

```powershell
.\thotp_downloader.exe --update
```

En Windows con el `.exe` de PyInstaller, la actualizacion se descarga, exige un
SHA256 valido y prepara `apply_update.bat` para reemplazar el ejecutable
cuando el proceso actual termine. Si se ejecuta desde `python thotp_downloader.py`,
solo descarga el nuevo `.exe` y muestra la ruta.

## N_m3u8DL-RE.exe

Para videos, el script busca `N_m3u8DL-RE.exe` en este orden:

1. Dentro del bundle generado con PyInstaller
2. Junto a `thotp_downloader.py`
3. En el `PATH` del sistema

Si no lo encuentra, mostrara un error claro y registrara el detalle en `logs/errors.txt`.

## Build Windows (.exe)

El proyecto incluye `build.bat` para generar un ejecutable portable con PyInstaller.

Instala dependencias:

```powershell
pip install -r requirements.txt
```

Verifica que `N_m3u8DL-RE.exe` este en la raiz del proyecto, junto a `build.bat`.

Compila:

```powershell
.\build.bat
```

Salida esperada:

```text
dist\thotp_downloader.exe
```

El build:

- Genera un unico ejecutable llamado `thotp_downloader.exe`.
- Incluye automaticamente `N_m3u8DL-RE.exe`.
- Incluye la carpeta `logs/`.
- Mantiene el backend Flask separado.
- Mantiene consola principal para ver progreso y mensajes del downloader.
- Oculta la consola secundaria innecesaria al ejecutar `N_m3u8DL-RE.exe` en Windows.

Uso del `.exe`:

```powershell
.\dist\thotp_downloader.exe "https://thotporn.tv/perfil/photo" --page 5
```

El archivo `license.txt` sigue siendo externo. Colocalo junto al `.exe` o ejecuta el `.exe` desde una carpeta que contenga `license.txt`, segun tu flujo actual.

## GUI Windows

La GUI es opcional y mantiene la CLI intacta. Ejecuta:

```powershell
python gui.py
```

Incluye campo de URL, selector de carpeta, soporte para `--page`, logs en
tiempo real, indicador FREE/PREMIUM, version actual, boton de actualizacion y
barra de progreso basica. La barra indica que hay una tarea en curso; la logica
actual del downloader no expone progreso granular por archivo.

Compilar la GUI con PyInstaller:

```powershell
python -m PyInstaller ^
    --clean ^
    --onefile ^
    --windowed ^
    --name thotp_downloader_gui ^
    --add-binary "N_m3u8DL-RE.exe;." ^
    --add-data "logs;logs" ^
    gui.py
```

Salida esperada:

```text
dist\thotp_downloader_gui.exe
```

El build CLI existente sigue usando `build.bat` y no cambia. Para videos,
mantén `N_m3u8DL-RE.exe` junto al proyecto al compilar la GUI.

## Uso

Perfil completo:

```powershell
python thotp_downloader.py "https://thotporn.tv/perfil"
```

En modo FREE, el perfil completo descarga fotos permitidas y omite el crawling de videos.

Todas las fotos de una pagina:

```powershell
python thotp_downloader.py "https://thotporn.tv/perfil/photo" --page 5
```

Varias paginas concretas:

```powershell
python thotp_downloader.py "https://thotporn.tv/perfil/photo" --page 3,5,8
```

Foto individual:

```powershell
python thotp_downloader.py "https://thotporn.tv/perfil/photo/123456"
```

Video individual:

```powershell
python thotp_downloader.py "https://thotporn.tv/perfil/video/123456"
```

Videos de una pagina:

```powershell
python thotp_downloader.py "https://thotporn.tv/perfil/video" --page 5
```

## Logs

Los errores importantes se escriben en:

```text
logs/errors.txt
```

Incluye fallos HTTP, respuestas invalidas, problemas de escritura y errores al ejecutar `N_m3u8DL-RE.exe`.

## Archivos Locales

Estos archivos y carpetas son datos locales de ejecucion y normalmente no deben versionarse:

- `Downloads/`
- `Logs/` y `logs/`
- `Temp/`
- `license.txt`
- `backend/licenses.json`
- `__pycache__/`
- `*.pyc`

El archivo `.gitignore` incluido excluye esos artefactos.
