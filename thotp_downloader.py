#!/usr/bin/env python3

import os
import sys
import time
import json
import re
import base64
import binascii
import shutil
import subprocess
import hashlib
import inspect
import platform
import logging
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse, urljoin
import requests
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

# ================= CONFIG =================


class DownloadControl:
    def __init__(self, state_callback=None):
        self._condition = threading.Condition()
        self._pause_requested = False
        self._cancel_requested = False
        self._paused = False
        self._cancel_logged = False
        self._state_callback = state_callback

    def request_pause(self):
        with self._condition:
            if not self._cancel_requested:
                self._pause_requested = True
                self._condition.notify_all()

    def request_resume(self):
        with self._condition:
            if not self._cancel_requested:
                self._pause_requested = False
                self._paused = False
                self._condition.notify_all()

    def request_cancel(self):
        with self._condition:
            self._cancel_requested = True
            self._pause_requested = False
            self._paused = False
            self._condition.notify_all()

    def should_drain(self):
        with self._condition:
            return self._pause_requested or self._cancel_requested

    def is_cancel_requested(self):
        with self._condition:
            return self._cancel_requested

    def wait_until_can_start(self):
        with self._condition:
            if self._cancel_requested:
                self._log_cancelled_once()
                return False

            if self._pause_requested:
                self._paused = True
                log_info("Descarga pausada")
                self._set_state("Pausado")

            while self._pause_requested and not self._cancel_requested:
                self._condition.wait()

            if self._cancel_requested:
                self._log_cancelled_once()
                return False

            return True

    def _log_cancelled_once(self):
        if not self._cancel_logged:
            self._cancel_logged = True
            log_info("Descarga cancelada por el usuario")
            self._set_state("Finalizado")

    def _set_state(self, state):
        if self._state_callback:
            self._state_callback(state)


def format_duration(seconds):
    total_seconds = max(0, int(seconds))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


@dataclass
class TaskStats:
    progress_callback: object = None
    ui_progress_callback: object = None
    single_page_task: bool = False
    page_progress_enabled: bool = False
    started_at: float = field(default_factory=time.monotonic)
    photos_new: int = 0
    photos_existing: int = 0
    photos_unavailable: int = 0
    videos_new: int = 0
    videos_existing: int = 0
    photos_current: int = 0
    photos_total: int = 0
    videos_current: int = 0
    videos_total: int = 0
    selected_pages_total: int = 0
    selected_page_index: int = 0
    current_page_total: int = 0
    current_page_processed: int = 0
    current_progress_label: str = ""

    def add_known_items(self, kind, count):
        if kind == "photos":
            self.photos_total += count
        elif kind == "videos":
            self.videos_total += count

    def mark_processed(self, kind, media_id=None, log_visible=True):
        if kind == "photos":
            self.photos_current += 1
            label = f"Foto {self.photos_current} de {self.photos_total}"
        else:
            self.videos_current += 1
            label = f"Video {self.videos_current} de {self.videos_total}"

        if media_id is not None:
            label = f"{label}: {media_id}"

        if log_visible:
            log_info(label)
        if self.progress_callback:
            self.progress_callback(label)
        self._mark_ui_progress(kind, label)

    def configure_selected_pages(self, selected_pages):
        self.selected_pages_total = len(selected_pages) if selected_pages else 0
        self.selected_page_index = 0

    def begin_page(self, kind, page_item_count):
        if self.selected_pages_total:
            self.selected_page_index += 1
        self.current_page_total = page_item_count
        self.current_page_processed = 0
        self.current_progress_label = self._progress_prefix(kind)
        self._emit_ui_progress(self.current_progress_label, self.current_percent())

    def complete_page(self, kind):
        if self.current_page_total:
            self.current_page_processed = self.current_page_total
        self.current_progress_label = self._progress_prefix(kind)
        self._emit_ui_progress(self.current_progress_label, self.current_percent())

    def current_percent(self):
        if not self.selected_pages_total:
            return None

        page_fraction = 1
        if self.current_page_total:
            page_fraction = min(1, self.current_page_processed / self.current_page_total)

        completed_pages = max(0, self.selected_page_index - 1)
        return min(
            100,
            ((completed_pages + page_fraction) / self.selected_pages_total) * 100,
        )

    def _mark_ui_progress(self, kind, label):
        self.current_page_processed += 1
        self.current_progress_label = label
        self._emit_ui_progress(label, self.current_percent())

    def _progress_prefix(self, kind):
        if kind == "photos":
            return f"Foto {self.photos_current} de {self.photos_total}"
        return f"Video {self.videos_current} de {self.videos_total}"

    def _emit_ui_progress(self, label, percent):
        if self.ui_progress_callback:
            self.ui_progress_callback({
                "label": label,
                "percent": percent,
            })

    def add_page_summary(self, kind, new_count, existing_count, unavailable_count=0):
        if kind == "photos":
            self.photos_new += new_count
            self.photos_existing += existing_count
            self.photos_unavailable += unavailable_count
        elif kind == "videos":
            self.videos_new += new_count
            self.videos_existing += existing_count

    def elapsed(self):
        return time.monotonic() - self.started_at

    def has_activity(self):
        return any((
            self.photos_new,
            self.photos_existing,
            self.photos_unavailable,
            self.videos_new,
            self.videos_existing,
        ))


def log_task_summary(stats):
    if stats.single_page_task:
        log_info(f"Tiempo total: {format_duration(stats.elapsed())}")
        log_info("Tarea finalizada")
        return

    if stats.photos_new or stats.photos_existing or stats.photos_unavailable:
        log_info(f"Total fotos nuevas: {stats.photos_new}")
        log_info(f"Total fotos ya existentes: {stats.photos_existing}")
        log_info(f"Total fotos no disponibles: {stats.photos_unavailable}")

    if stats.videos_new or stats.videos_existing:
        log_info(f"Total videos nuevos: {stats.videos_new}")
        log_info(f"Total videos ya existentes: {stats.videos_existing}")

    log_info(f"Tiempo total: {format_duration(stats.elapsed())}")
    log_info("Tarea finalizada")


def is_single_manual_page_task(typ, item_id, selected_pages):
    return (
        typ in ("photo", "video")
        and item_id is None
        and selected_pages is not None
        and len(selected_pages) == 1
    )


@dataclass(frozen=True)
class AppConfig:
    version: str
    environment: str
    retry: int
    script_dir: str
    runtime_dir: str
    base_resource_dir: str
    user_data_dir: str
    update_temp_dir: str
    cdn_image_prefix: str
    downloads_root: str
    free_usage_file: str
    license_file: str
    api_url: str
    version_url: str
    logs_dir: str
    error_log_file: str
    nm3u8_exe_name: str


APP_VERSION = "1.0.0"
RETRY = 3
APP_NAME = "THOTP Downloader"
ENVIRONMENT_ENV = "THOTP_ENV"
API_URL_ENV = "THOTP_API_URL"
VERSION_URL_ENV = "THOTP_VERSION_URL"
USER_DATA_DIR_ENV = "THOTP_USER_DATA_DIR"
DOWNLOADS_DIR_ENV = "THOTP_DOWNLOADS_DIR"
DEV_API_URL = "http://127.0.0.1:5000/verify"
DEV_VERSION_URL = "http://127.0.0.1:5000/version.json"
PROD_API_URL = "https://license.example.com/verify"
PROD_VERSION_URL = "https://license.example.com/version.json"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else SCRIPT_DIR
BASE_RESOURCE_DIR = getattr(sys, "_MEIPASS", RUNTIME_DIR)


def normalized_environment():
    value = os.environ.get(ENVIRONMENT_ENV, "").strip().lower()
    if value in ("dev", "development", "local"):
        return "development"
    if value in ("prod", "production", "release"):
        return "production"
    return "production" if getattr(sys, "frozen", False) else "development"


def default_user_data_dir(environment):
    override = os.environ.get(USER_DATA_DIR_ENV)
    if override:
        return os.path.abspath(os.path.expanduser(override))

    if environment == "development":
        return SCRIPT_DIR

    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if root:
            return os.path.join(root, APP_NAME)

    if sys.platform == "darwin":
        return os.path.join(Path.home(), "Library", "Application Support", APP_NAME)

    root = os.environ.get("XDG_DATA_HOME") or os.path.join(Path.home(), ".local", "share")
    return os.path.join(root, "thotp-downloader")


def default_downloads_root():
    override = os.environ.get(DOWNLOADS_DIR_ENV)
    if override:
        return os.path.abspath(os.path.expanduser(override))

    downloads_dir = Path.home() / "Downloads"
    if downloads_dir.exists():
        return str(downloads_dir / APP_NAME)

    return os.path.join(USER_DATA_DIR, "Downloads")


def endpoint_url(dev_url, prod_url, env_name, environment):
    override = os.environ.get(env_name)
    if override:
        return override.strip()
    return dev_url if environment == "development" else prod_url


APP_ENVIRONMENT = normalized_environment()
USER_DATA_DIR = default_user_data_dir(APP_ENVIRONMENT)
UPDATE_TEMP_DIR = os.path.join(USER_DATA_DIR, "updates")
CDN_IMAGE_PREFIX = "https://image-cdn-thotporntv.b-cdn.net/"
DOWNLOADS_ROOT = default_downloads_root()
FREE_USAGE_FILE = os.path.join(USER_DATA_DIR, "free_usage.json")
LICENSE_FILE = os.path.join(USER_DATA_DIR, "license.txt")
API_URL = endpoint_url(DEV_API_URL, PROD_API_URL, API_URL_ENV, APP_ENVIRONMENT)
VERSION_URL = endpoint_url(DEV_VERSION_URL, PROD_VERSION_URL, VERSION_URL_ENV, APP_ENVIRONMENT)
LOGS_DIR = os.path.join(USER_DATA_DIR, "logs")
ERROR_LOG_FILE = os.path.join(LOGS_DIR, "errors.txt")
NM3U8_EXE_NAME = "N_m3u8DL-RE.exe"
FFMPEG_EXE_NAME = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
VIDEO_SUCCESS_PAUSE_SECONDS = 3
RATE_LIMIT_RETRY_SECONDS = 65
CONFIG = AppConfig(
    version=APP_VERSION,
    environment=APP_ENVIRONMENT,
    retry=RETRY,
    script_dir=SCRIPT_DIR,
    runtime_dir=RUNTIME_DIR,
    base_resource_dir=BASE_RESOURCE_DIR,
    user_data_dir=USER_DATA_DIR,
    update_temp_dir=UPDATE_TEMP_DIR,
    cdn_image_prefix=CDN_IMAGE_PREFIX,
    downloads_root=DOWNLOADS_ROOT,
    free_usage_file=FREE_USAGE_FILE,
    license_file=LICENSE_FILE,
    api_url=API_URL,
    version_url=VERSION_URL,
    logs_dir=LOGS_DIR,
    error_log_file=ERROR_LOG_FILE,
    nm3u8_exe_name=NM3U8_EXE_NAME,
)


def setup_logging():
    os.makedirs(CONFIG.logs_dir, exist_ok=True)
    logging.basicConfig(
        filename=CONFIG.error_log_file,
        level=logging.ERROR,
        format="%(asctime)s [%(levelname)s] %(message)s",
        encoding="utf-8"
    )


def detect_nm3u8():
    bundled_path = os.path.join(CONFIG.base_resource_dir, CONFIG.nm3u8_exe_name)
    if os.path.exists(bundled_path):
        return bundled_path

    local_path = os.path.join(CONFIG.script_dir, CONFIG.nm3u8_exe_name)

    if os.path.exists(local_path):
        return local_path

    path_exe = shutil.which(CONFIG.nm3u8_exe_name)
    if path_exe:
        return path_exe

    path_cmd = shutil.which("N_m3u8DL-RE")
    if path_cmd:
        return path_cmd

    return None


def detect_ffmpeg():
    bundled_path = os.path.join(CONFIG.base_resource_dir, FFMPEG_EXE_NAME)
    if os.path.exists(bundled_path):
        return bundled_path

    local_path = os.path.join(CONFIG.script_dir, FFMPEG_EXE_NAME)
    if os.path.exists(local_path):
        return local_path

    return shutil.which(FFMPEG_EXE_NAME) or shutil.which("ffmpeg")


def subprocess_creation_flags():
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        return subprocess.CREATE_NO_WINDOW

    return 0


def nm3u8_available():
    return bool(NM3U8_PATH and os.path.isfile(NM3U8_PATH))


def ffmpeg_available():
    return bool(FFMPEG_PATH and os.path.isfile(FFMPEG_PATH))


def profile_premium_message():
    return "Para descargar el perfil completo, active PREMIUM."


def video_premium_message():
    return "Videos solo en PREMIUM"


def active_license_label():
    if IS_PREMIUM:
        return "Licencia: PREMIUM"

    return "Licencia: FREE (5 fotos nuevas)"


def log_update_check_failed():
    log_info("No se pudo comprobar si hay actualizaciones.")


def sanitize_signed_urls(text):
    if not text:
        return text

    text = re.sub(r'https?://\S*(?:time=|sig=|sig2=)\S*', '[URL firmada ocultada]', str(text))
    text = re.sub(r'https://thotporn\.tv/m3u8/\S+', '[URL m3u8 ocultada]', text)
    return text


def is_rate_limit_error(text):
    if not text:
        return False

    return bool(re.search(r'\b429\b|Too Many Requests', str(text), re.I))


FFMPEG_MISSING_REPORTED = False


def report_missing_ffmpeg_once():
    global FFMPEG_MISSING_REPORTED

    if FFMPEG_MISSING_REPORTED:
        return

    FFMPEG_MISSING_REPORTED = True
    logging.error("ffmpeg no encontrado junto al ejecutable ni en PATH")
    log_error("ffmpeg no esta instalado o no esta disponible en PATH.")
    log_info("Instale ffmpeg y reinicie la aplicacion.")


def log_http_error(context, url, error):
    response = getattr(error, "response", None)
    status_code = response.status_code if response is not None else None

    if status_code in (403, 404):
        message = f"{context}: HTTP {status_code} en {url}"
    else:
        message = f"{context}: {error}"

    logging.exception(message)
    log_warn(sanitize_signed_urls(message))


setup_logging()
NM3U8_PATH = detect_nm3u8()
FFMPEG_PATH = detect_ffmpeg()

HEADERS = {"User-Agent": "Mozilla/5.0"}
AJAX_HEADERS = {"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# ================= UPDATE =================

def parse_version(version):
    parts = []

    for part in str(version).split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        parts.append(int(digits or 0))

    return tuple(parts)


def is_newer_version(remote_version, local_version=None):
    local = parse_version(local_version or CONFIG.version)
    remote = parse_version(remote_version)
    max_len = max(len(local), len(remote))

    local += (0,) * (max_len - len(local))
    remote += (0,) * (max_len - len(remote))

    return remote > local


def fetch_update_metadata(version_url=None):
    url = version_url or CONFIG.version_url

    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        logging.exception("No se pudo consultar version remota: %s", url)
        log_update_check_failed()
        return None
    except ValueError as e:
        logging.exception("Respuesta version.json invalida: %s", url)
        log_info("No se pudo comprobar si hay actualizaciones.")
        return None

    if not isinstance(data, dict):
        logging.error("version.json no es un objeto JSON: %s", url)
        log_warn("Respuesta de actualizacion invalida")
        return None

    return data


def resolve_download_url(metadata, version_url=None):
    download_url = metadata.get("download_url")
    if not isinstance(download_url, str) or not download_url.strip():
        return None

    return urljoin(version_url or CONFIG.version_url, download_url.strip())


def get_available_update(version_url=None):
    metadata = fetch_update_metadata(version_url=version_url)
    if not metadata:
        return None

    remote_version = metadata.get("version")
    if not remote_version or not is_newer_version(remote_version):
        return None

    download_url = resolve_download_url(metadata, version_url=version_url)
    if not download_url:
        logging.error("version.json no incluye download_url valido")
        log_warn("Hay una version nueva, pero falta download_url")
        return None

    sha256 = metadata.get("sha256")
    if not isinstance(sha256, str) or not sha256.strip():
        logging.error("version.json no incluye sha256 valido")
        log_warn("Hay una version nueva, pero falta sha256")
        return None

    metadata["download_url"] = download_url
    metadata["sha256"] = sha256.strip()
    return metadata


def print_update_notice(metadata):
    print(
        "[UPDATE] Nueva version disponible: "
        f"{metadata.get('version')} (actual: {CONFIG.version})"
    )
    notes = metadata.get("notes")
    if notes:
        print(f"[UPDATE] {notes}")
    print("[UPDATE] Ejecuta este programa con --update para descargarla.")


def check_for_update_notice():
    metadata = get_available_update()
    if metadata:
        print_update_notice(metadata)


def current_executable_path():
    if getattr(sys, "frozen", False):
        return sys.executable

    return None


def calculate_file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def download_update_file(download_url, expected_sha256=None):
    parsed = urlparse(download_url)
    if parsed.scheme not in ("http", "https"):
        log_error("URL de actualizacion no permitida")
        return None

    os.makedirs(CONFIG.update_temp_dir, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix="thotp_downloader_",
        suffix=".exe",
        dir=CONFIG.update_temp_dir,
    )
    os.close(fd)

    try:
        with requests.get(download_url, stream=True, timeout=30) as response:
            response.raise_for_status()
            with open(temp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

        if expected_sha256:
            actual_sha256 = calculate_file_sha256(temp_path)
            if actual_sha256.lower() != expected_sha256.lower():
                logging.error(
                    "SHA256 de actualizacion invalido. Esperado=%s Actual=%s",
                    expected_sha256,
                    actual_sha256,
                )
                log_error("La actualizacion descargada no paso la verificacion SHA256")
                os.remove(temp_path)
                return None

        return temp_path
    except requests.RequestException as e:
        logging.exception("No se pudo descargar actualizacion: %s", download_url)
        log_error(f"No pude descargar la actualizacion: {e}")
    except OSError as e:
        logging.exception("No se pudo escribir actualizacion")
        log_error(f"No pude guardar la actualizacion: {e}")

    try:
        os.remove(temp_path)
    except OSError:
        pass

    return None


def prepare_windows_replacement(new_exe_path, current_exe_path):
    os.makedirs(CONFIG.update_temp_dir, exist_ok=True)
    update_script = os.path.join(CONFIG.update_temp_dir, "apply_update.bat")
    backup_path = current_exe_path + ".old"

    script = f"""@echo off
setlocal
timeout /t 2 /nobreak >nul
move /y "{current_exe_path}" "{backup_path}" >nul
move /y "{new_exe_path}" "{current_exe_path}" >nul
start "" "{current_exe_path}"
del "%~f0"
"""

    try:
        with open(update_script, "w", encoding="utf-8") as f:
            f.write(script)
    except OSError as e:
        logging.exception("No se pudo preparar script de actualizacion")
        log_error(f"No pude preparar el reemplazo del .exe: {e}")
        return None

    return update_script


def apply_update(metadata):
    download_url = metadata["download_url"]
    expected_sha256 = metadata["sha256"]

    print(f"[UPDATE] Descargando version {metadata.get('version')}...")
    new_exe_path = download_update_file(download_url, expected_sha256=expected_sha256)
    if not new_exe_path:
        return False

    current_exe_path = current_executable_path()
    if not current_exe_path:
        print(f"[UPDATE] Descarga completa: {new_exe_path}")
        print("[UPDATE] El reemplazo automatico solo aplica al .exe de PyInstaller.")
        return True

    if os.name != "nt":
        print(f"[UPDATE] Descarga completa: {new_exe_path}")
        print("[UPDATE] Reemplaza manualmente el ejecutable actual al cerrar el programa.")
        return True

    update_script = prepare_windows_replacement(new_exe_path, current_exe_path)
    if not update_script:
        return False

    print("[UPDATE] Actualizacion preparada. El programa se cerrara para reemplazar el .exe.")
    subprocess.Popen(
        [update_script],
        shell=True,
        creationflags=subprocess_creation_flags()
    )
    return True


def run_update():
    metadata = get_available_update()
    if not metadata:
        print(f"[UPDATE] No hay actualizaciones disponibles (version actual: {CONFIG.version}).")
        return False

    return apply_update(metadata)

# ================= LICENSE =================

def get_hwid():

    raw = (
        platform.node() +
        platform.system() +
        platform.processor()
    )

    return hashlib.sha256(raw.encode()).hexdigest()


def load_license():

    if not os.path.exists(LICENSE_FILE):
        return None

    try:
        with open(LICENSE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        logging.exception("No se pudo leer el archivo de licencia: %s", LICENSE_FILE)
        return None


def verify_license_online():

    license_key = load_license()

    if not license_key:
        return False

    hwid = get_hwid()

    try:

        r = requests.post(
            API_URL,
            json={
                "license_key": license_key,
                "hwid": hwid
            },
            timeout=10
        )

        data = r.json()

        if data.get("success") and data.get("premium"):
            print("OK", data.get("message", "Licencia PREMIUM valida"))
            return True

        print("[LICENSE]", data.get("message"))
        return False

    except requests.RequestException as e:

        logging.exception("Error conectando al servidor de licencias: %s", API_URL)
        print(f"[LICENSE] No se pudo conectar al servidor: {e}")
        return False

    except ValueError as e:

        logging.exception("Respuesta invalida del servidor de licencias")
        print(f"[LICENSE] Respuesta invalida del servidor: {e}")
        return False


# ================= FREE USAGE =================

def read_free_usage():
    try:
        with open(FREE_USAGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logging.exception("No se pudo leer uso FREE: %s", FREE_USAGE_FILE)
        return None

    if not isinstance(data, dict):
        return None

    return data


def load_free_photo_count():
    usage = read_free_usage()
    if not usage:
        return 0

    if usage.get("hwid") != get_hwid():
        return 0

    try:
        count = int(usage.get("photos_new", 0))
    except (TypeError, ValueError):
        return 0

    return max(0, count)


def save_free_photo_count(count):
    usage_dir = os.path.dirname(FREE_USAGE_FILE) or "."
    temp_path = None

    try:
        usage = {
            "hwid": get_hwid(),
            "photos_new": max(0, int(count)),
        }
        os.makedirs(usage_dir, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            prefix="free_usage_",
            suffix=".json.tmp",
            dir=usage_dir,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(usage, f, indent=2)
            f.write("\n")
        os.replace(temp_path, FREE_USAGE_FILE)
        return True
    except (OSError, TypeError, ValueError):
        logging.exception("No se pudo guardar uso FREE: %s", FREE_USAGE_FILE)
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass
        return False


def increment_free_photo_count():
    global downloaded_total

    downloaded_total += 1
    save_free_photo_count(downloaded_total)


# ================= PREMIUM CONFIG =================

IS_PREMIUM = verify_license_online()

FREE_PHOTO_LIMIT = 5
downloaded_total = 0 if IS_PREMIUM else load_free_photo_count()

CURRENT_SLEEP = 0.8 if not IS_PREMIUM else 0.15
PHOTOS_WORKERS = 1 if not IS_PREMIUM else 3

# ================= LOG =================

def log_info(msg): print(f"[info] {msg}")
def log_ok(msg): print(f"OK {msg}")
def log_warn(msg): print(f"[warn] {msg}")
def log_error(msg): print(f"[error] {msg}")
def log_exception(msg): logging.exception(msg)
def log_debug(msg): print(f"[debug] {msg}")


VIDEO_DOWNLOAD_STATE = threading.local()


def set_last_video_result(result):
    VIDEO_DOWNLOAD_STATE.last_result = result


def get_last_video_result():
    return getattr(VIDEO_DOWNLOAD_STATE, "last_result", None)

# ================= UTILS =================

def decode_thotporn_src(encoded_str):
    try:
        stage1 = encoded_str[16:]
        stage2 = stage1[::-1]
        stage3 = re.sub(r'[^A-Za-z0-9+/=]', '', stage2)
        stage3 += '=' * (-len(stage3) % 4)
        decoded = base64.b64decode(stage3).decode('latin-1')
        match = re.search(r'(https://thotporn\.tv/m3u8/.+)', decoded)
        return (match.group(1), decoded) if match else (None, decoded)
    except (TypeError, binascii.Error, UnicodeDecodeError):
        logging.exception("No se pudo decodificar source m3u8")
        return None, None

def safe_get_json(url):
    for i in range(RETRY):
        try:
            r = SESSION.get(url, headers=AJAX_HEADERS, timeout=20)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            log_http_error(f"No pude obtener JSON ({i + 1}/{RETRY})", url, e)
            time.sleep(1)
        except ValueError as e:
            logging.exception("JSON invalido en respuesta: %s", url)
            log_warn(f"Respuesta JSON invalida ({i + 1}/{RETRY}): {e}")
            time.sleep(1)
    return None

PHOTO_DOWNLOAD_DIAGNOSTIC = threading.local()


def safe_get_stream(url, dest):
    for attempt in range(RETRY):
        try:
            r = SESSION.get(url, timeout=20)
            r.raise_for_status()
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(r.content)
            return dest
        except requests.RequestException as e:
            response = getattr(e, "response", None)
            diagnostic = getattr(PHOTO_DOWNLOAD_DIAGNOSTIC, "value", None)
            if diagnostic and response is not None and response.status_code == 403:
                photo_id = diagnostic.get("photo_id")
                ext = diagnostic.get("extension")
                log_debug(f"Descargando foto {photo_id}")
                log_debug(f"URL: {url}")
                log_debug(f"Extension: {ext}")
                log_warn(f"Foto {photo_id} - HTTP 403 (intento {attempt + 1}/{RETRY})")
            log_http_error("No pude descargar archivo", url, e)
            time.sleep(1)
        except OSError as e:
            logging.exception("Error escribiendo archivo: %s", dest)
            log_error(f"No pude escribir {dest}: {e}")
            time.sleep(1)
    return None

def extract_profile_from_url(url):
    parts = urlparse(url).path.strip("/").split("/")
    profile = parts[0]
    typ = parts[1] if len(parts) > 1 else "profile"
    item_id = parts[2] if len(parts) > 2 else None
    return profile, typ, item_id

# ================= IMAGE =================

IMAGE_FIELDS = ("player", "banner", "image", "origin_url", "thumbnail")
EMPTY_IMAGE_HOSTS = {
    "image-cdn.thotporn.tv",
    urlparse(CDN_IMAGE_PREFIX).netloc,
}


def is_empty_image_url(url):
    if not isinstance(url, str):
        return True

    clean_url = url.strip()
    if not clean_url:
        return True

    parsed = urlparse(clean_url)
    if parsed.scheme and parsed.netloc in EMPTY_IMAGE_HOSTS:
        return parsed.path in ("", "/")

    return False


def log_photo_unavailable(photo_id):
    log_warn(f"Foto {photo_id} eliminada o no disponible")


def photo_item_download_details(item, folder):
    media_id = str(item.get("id"))
    url = item_image_url(item)

    if is_empty_image_url(url):
        return {
            "media_id": media_id,
            "url": None,
            "extension": None,
            "path": None,
            "unavailable": True,
        }

    if not url.startswith("http"):
        url = CDN_IMAGE_PREFIX + url.lstrip("/")

    if is_empty_image_url(url):
        return {
            "media_id": media_id,
            "url": url,
            "extension": None,
            "path": None,
            "unavailable": True,
        }

    ext = os.path.splitext(url.split("?")[0])[1]

    if not ext:
        ext = ".webp"

    path = os.path.join(
        folder,
        "photos",
        f"{media_id}{ext}"
    )

    return {
        "media_id": media_id,
        "url": url,
        "extension": ext,
        "path": path,
        "unavailable": False,
    }


def log_photo_page_summary(new_count, existing_count, unavailable_count):
    if new_count or existing_count or unavailable_count:
        log_info(f"Fotos nuevas: {new_count}")
        log_info(f"Fotos ya existentes: {existing_count}")
        log_info(f"Fotos no disponibles: {unavailable_count}")


def log_video_page_summary(new_count, existing_count):
    if new_count or existing_count:
        log_info(f"Videos nuevos: {new_count}")
        log_info(f"Videos ya existentes: {existing_count}")


def extract_total_pages(data):
    if not isinstance(data, dict):
        return None

    candidates = (
        data.get("total_pages"),
        data.get("last_page"),
        data.get("pages"),
    )

    meta = data.get("meta")
    if isinstance(meta, dict):
        candidates += (
            meta.get("total_pages"),
            meta.get("last_page"),
            meta.get("pages"),
        )

    pagination = data.get("pagination")
    if isinstance(pagination, dict):
        candidates += (
            pagination.get("total_pages"),
            pagination.get("last_page"),
            pagination.get("pages"),
        )

    for value in candidates:
        try:
            total_pages = int(value)
        except (TypeError, ValueError):
            continue
        if total_pages > 0:
            return total_pages

    return None


def extract_api_items(data):
    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return data

    for key in ("data", "items", "results"):
        items = data.get(key)
        if isinstance(items, list):
            return items

    return data


def item_image_url(item):
    for k in IMAGE_FIELDS:
        v = item.get(k)
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("http"):
                return v
            if v.startswith("/") or v.startswith("storage/"):
                return CDN_IMAGE_PREFIX + v.lstrip("/")
    return None

def existing_photo_path(folder, photo_id):
    photos_dir = os.path.join(folder, "photos")
    if not os.path.isdir(photos_dir):
        return None

    prefix = f"{photo_id}."
    for name in os.listdir(photos_dir):
        if name == str(photo_id) or name.startswith(prefix):
            return os.path.join(photos_dir, name)

    return None

def fetch_single_photo_html(profile, photo_id):

    url = f"https://thotporn.tv/{profile}/photo/{photo_id}"

    try:

        r = SESSION.get(url, timeout=20)
        r.raise_for_status()

    except requests.RequestException as e:

        logging.exception("No se pudo abrir HTML de foto %s", photo_id)
        log_error(f"No pude abrir HTML: {e}")
        return None

    text = r.text

    # buscar data-src
    m = re.search(
        r'data-src=["\']([^"\']+)["\']',
        text,
        re.I
    )

    if not m:

        log_warn("No encontre imagen en HTML")
        return None

    img_url = m.group(1).strip()

    if img_url.startswith("/"):
        img_url = CDN_IMAGE_PREFIX + img_url.lstrip("/")

    return img_url

def download_photo_item(item, folder, log_media_id=True):
    global downloaded_total

    if not IS_PREMIUM and downloaded_total >= FREE_PHOTO_LIMIT:
        return None

    details = photo_item_download_details(item, folder)
    media_id = details["media_id"]

    if details["unavailable"]:
        log_photo_unavailable(media_id)
        return None

    url = details["url"]
    ext = details["extension"]
    path = details["path"]

    if os.path.exists(path):
        return path

    if log_media_id:
        print(f"[photo] {media_id}")
    previous_diagnostic = getattr(PHOTO_DOWNLOAD_DIAGNOSTIC, "value", None)
    PHOTO_DOWNLOAD_DIAGNOSTIC.value = {"photo_id": media_id, "extension": ext}
    try:
        downloaded_path = safe_get_stream(url, path)
    finally:
        if previous_diagnostic is None:
            del PHOTO_DOWNLOAD_DIAGNOSTIC.value
        else:
            PHOTO_DOWNLOAD_DIAGNOSTIC.value = previous_diagnostic

    if downloaded_path:
        if IS_PREMIUM:
            downloaded_total += 1
        else:
            increment_free_photo_count()

    return downloaded_path

# ================= VIDEO =================

def download_video_item(item, folder, profile, log_media_id=True):
    set_last_video_result(None)

    if not IS_PREMIUM:
        log_info(video_premium_message())
        return None

    vid_id = str(item.get("id"))
    dest = os.path.join(folder, "videos", f"{vid_id}.mp4")

    if log_media_id:
        print(f"[video] {vid_id}")

    if os.path.exists(dest):
        set_last_video_result("existing")
        print("OK Ya existe")
        return dest

    if not nm3u8_available():
        logging.error("N_m3u8DL-RE.exe no encontrado junto al script ni en PATH")
        log_error(
            "No se encontro N_m3u8DL-RE.exe junto al script ni en PATH. "
            "Colocalo junto a thotp_downloader.py o agregalo al PATH."
        )
        return None

    if not ffmpeg_available():
        report_missing_ffmpeg_once()
        return None

    page_url = f"https://thotporn.tv/{profile}/video/{vid_id}"

    try:
        r = SESSION.get(page_url, timeout=20)
        r.raise_for_status()

        video_match = re.search(r'data-video="(.+?)"', r.text)
        if not video_match:
            log_warn(f"No encontre data-video para video {vid_id}")
            return None
        data = json.loads(video_match.group(1).replace("&quot;", '"'))
        m3u8, _ = decode_thotporn_src(data["source"][0]["src"])
        if not m3u8:
            log_warn(f"No pude decodificar m3u8 para video {vid_id}")
            return None
    except (requests.RequestException, ValueError, KeyError, IndexError) as e:
        if isinstance(e, requests.RequestException):
            log_http_error(f"No pude preparar el video {vid_id}", page_url, e)
        else:
            logging.exception("No se pudo preparar descarga de video %s", vid_id)
            log_error(f"No pude preparar el video {vid_id}: {e}")
        return None

    for attempt in range(5):
        try:
            env = os.environ.copy()
            ffmpeg_dir = os.path.dirname(FFMPEG_PATH) if FFMPEG_PATH else None
            if ffmpeg_dir:
                env["PATH"] = ffmpeg_dir + os.pathsep + env.get("PATH", "")

            subprocess.run(
                [NM3U8_PATH, m3u8, "--save-dir", os.path.dirname(dest), "--save-name", vid_id],
                check=True,
                creationflags=subprocess_creation_flags(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            print("OK Descargado")
            set_last_video_result("downloaded")
            return dest
        except (OSError, subprocess.CalledProcessError) as e:
            output = getattr(e, "stdout", "") or getattr(e, "output", "")
            logging.exception("Fallo N_m3u8DL-RE para video %s. Salida: %s", vid_id, output)
            log_warn(f"Fallo la descarga del video {vid_id} (intento {attempt + 1}/5)")
            if is_rate_limit_error(output) or is_rate_limit_error(e):
                log_warn("Limite temporal del servidor detectado.")
                log_info(f"Esperando {RATE_LIMIT_RETRY_SECONDS} segundos antes de reintentar...")
                time.sleep(RATE_LIMIT_RETRY_SECONDS)
                continue
            time.sleep(1)

    log_error(f"No se pudo descargar el video {vid_id} despues de 5 intentos")
    return None


def download_photo_collection_item(item, folder):
    if "log_media_id" in inspect.signature(download_photo_item).parameters:
        return download_photo_item(item, folder, log_media_id=False)
    return download_photo_item(item, folder)


def download_video_collection_item(item, folder, profile):
    if "log_media_id" in inspect.signature(download_video_item).parameters:
        return download_video_item(item, folder, profile, log_media_id=False)
    return download_video_item(item, folder, profile)

# ================= SINGLE ITEMS =================

def process_single_photo_by_id(profile, photo_id, profile_folder):
    existing_path = existing_photo_path(profile_folder, photo_id)

    if existing_path:
        print(f"[photo] {photo_id}")
        print("OK Ya existe")
        return existing_path

    if not IS_PREMIUM and downloaded_total >= FREE_PHOTO_LIMIT:
        log_info("Limite FREE alcanzado (5 fotos nuevas)")
        return None

    print(f"[photo] {photo_id}")

    photo_url = f"https://thotporn.tv/{profile}/photo/{photo_id}"

    try:
        r = SESSION.get(photo_url, timeout=15)
        r.raise_for_status()

    except requests.RequestException as e:
        logging.exception("No se pudo abrir pagina de foto %s", photo_id)
        log_error(f"No pude abrir la pagina: {e}")
        return None

    html = r.text

    # Buscar URL real
    m = re.search(
        r'data-src=["\']([^"\']*)["\']',
        html
    )

    if not m:
        log_photo_unavailable(photo_id)
        return None

    img_url = m.group(1).strip()

    if not img_url.startswith("http"):
        img_url = CDN_IMAGE_PREFIX + img_url.lstrip("/")

    if is_empty_image_url(img_url):
        log_photo_unavailable(photo_id)
        return None

    # detectar extension real
    ext = os.path.splitext(
        img_url.split("?")[0]
    )[1]

    if not ext:
        ext = ".webp"

    dest = os.path.join(
        profile_folder,
        "photos",
        f"{photo_id}{ext}"
    )

    if os.path.exists(dest):
        print("OK Ya existe")
        return dest

    previous_diagnostic = getattr(PHOTO_DOWNLOAD_DIAGNOSTIC, "value", None)
    PHOTO_DOWNLOAD_DIAGNOSTIC.value = {"photo_id": photo_id, "extension": ext}
    try:
        downloaded_path = safe_get_stream(img_url, dest)
    finally:
        if previous_diagnostic is None:
            del PHOTO_DOWNLOAD_DIAGNOSTIC.value
        else:
            PHOTO_DOWNLOAD_DIAGNOSTIC.value = previous_diagnostic

    if not downloaded_path:
        return None

    if not IS_PREMIUM:
        increment_free_photo_count()

    print("OK Descargado")

    return downloaded_path


def process_single_video_by_id(profile, video_id, profile_folder):
    """
    Descarga un solo video usando su ID exacto
    """
    if not IS_PREMIUM:
        return download_video_item(
            {"id": video_id},
            profile_folder,
            profile
        )

    item = {
        "id": video_id
    }

    return download_video_item(
        item,
        profile_folder,
        profile
    )

# ================= CRAWL =================

def build_api_url(profile, page, kind):
    return f"https://thotporn.tv/{profile}?page={page}&type={kind}&order=0"

def crawl_collection(profile, kind, out_base, selected_pages=None, control=None, stats=None):

    global downloaded_total

    folder = os.path.join(out_base, profile)
    results = []

    def finish_crawl():
        return results

    if selected_pages:
        pages = selected_pages
    else:
        pages = range(1, 1000)

    if stats and stats.page_progress_enabled and selected_pages and not stats.selected_pages_total:
        stats.configure_selected_pages(selected_pages)

    if not IS_PREMIUM and kind == "videos":
        log_info(video_premium_message())
        return finish_crawl()

    for page in pages:
        if control and not control.wait_until_can_start():
            return finish_crawl()

        # limite FREE global
        if (
            not IS_PREMIUM
            and kind == "photos"
            and downloaded_total >= FREE_PHOTO_LIMIT
        ):
            log_info("Limite FREE alcanzado (5 fotos nuevas)")
            break

        url = build_api_url(profile, page, kind)

        raw_data = safe_get_json(url)
        total_pages = extract_total_pages(raw_data)
        if total_pages:
            print(f"[info] Pagina {page} de {total_pages}")
        else:
            print(f"[info] Pagina {page}")

        data = extract_api_items(raw_data)

        if control and not control.wait_until_can_start():
            return finish_crawl()

        if data == []:
            if stats and stats.page_progress_enabled and selected_pages:
                stats.add_known_items(kind, 0)
                stats.begin_page(kind, 0)
                stats.complete_page(kind)
            log_info(f"No se encontraron elementos en la pagina {page}.")
            if selected_pages:
                continue
            break

        if not data:
            break

        # =========================
        # FREE FILTER
        # =========================

        page_new_count = 0
        page_existing_count = 0
        page_unavailable_count = 0

        if stats and isinstance(data, list):
            stats.add_known_items(kind, len(data))
            stats.begin_page(kind, len(data))

        if not IS_PREMIUM and kind == "photos":

            filtered = []

            for item in data:

                details = photo_item_download_details(item, folder)
                media_id = details["media_id"]

                if details["unavailable"]:
                    page_unavailable_count += 1
                    log_photo_unavailable(media_id)
                    if stats:
                        stats.mark_processed("photos", media_id)
                    continue

                # saltar existentes
                if os.path.exists(details["path"]):
                    page_existing_count += 1
                    if stats:
                        stats.mark_processed("photos", media_id, log_visible=False)
                    continue

                filtered.append(item)

                if len(filtered) >= (
                    FREE_PHOTO_LIMIT - downloaded_total
                ):
                    break

            data = filtered

        # =========================
        # PHOTOS
        # =========================

        if kind == "photos":

            if IS_PREMIUM:

                details_by_index = []
                for item in data:
                    details = photo_item_download_details(item, folder)
                    if details["unavailable"]:
                        page_unavailable_count += 1
                        details["was_existing"] = False
                    else:
                        was_existing = os.path.exists(details["path"])
                        details["was_existing"] = was_existing
                    if details.get("was_existing"):
                        page_existing_count += 1
                    details_by_index.append(details)

                pending_items = iter(zip(data, details_by_index))
                pending_exhausted = False

                with ThreadPoolExecutor(max_workers=PHOTOS_WORKERS) as ex:
                    active_futures = {}

                    while active_futures or not pending_exhausted:
                        while (
                            not pending_exhausted
                            and len(active_futures) < PHOTOS_WORKERS
                            and not (control and control.should_drain())
                        ):
                            if control and not control.wait_until_can_start():
                                return finish_crawl()

                            try:
                                item, details = next(pending_items)
                            except StopIteration:
                                pending_exhausted = True
                                break

                            future = ex.submit(download_photo_collection_item, item, folder)
                            active_futures[future] = details

                        if active_futures:
                            done, _ = wait(
                                active_futures,
                                return_when=FIRST_COMPLETED,
                            )

                            for f in done:
                                details = active_futures.pop(f)
                                r = f.result()

                                if r:
                                    results.append(r)
                                    if not details["unavailable"] and not details["was_existing"]:
                                        page_new_count += 1
                                if stats:
                                    stats.mark_processed(
                                        "photos",
                                        details["media_id"],
                                        log_visible=not details.get("was_existing"),
                                    )

                            continue

                        if control and control.should_drain():
                            if not control.wait_until_can_start():
                                return finish_crawl()
                            continue

                        break

            else:

                for item in data:
                    if control and not control.wait_until_can_start():
                        return finish_crawl()

                    if downloaded_total >= FREE_PHOTO_LIMIT:
                        log_info("Limite FREE alcanzado (5 fotos nuevas)")
                        break

                    r = download_photo_collection_item(
                        item,
                        folder,
                    )

                    if r:
                        results.append(r)
                        page_new_count += 1
                    if stats:
                        stats.mark_processed("photos", str(item.get("id")))

                    time.sleep(CURRENT_SLEEP)

            log_photo_page_summary(
                page_new_count,
                page_existing_count,
                page_unavailable_count,
            )
            if stats:
                stats.complete_page("photos")
            if stats:
                stats.add_page_summary(
                    "photos",
                    page_new_count,
                    page_existing_count,
                    page_unavailable_count,
                )

        # =========================
        # VIDEOS
        # =========================

        else:

            for item in data:
                if control and not control.wait_until_can_start():
                    return finish_crawl()

                vid_id = str(item.get("id"))
                video_path = os.path.join(folder, "videos", f"{vid_id}.mp4")
                was_existing = os.path.exists(video_path)

                set_last_video_result(None)
                r = download_video_collection_item(
                    item,
                    folder,
                    profile,
                )

                if r:
                    results.append(r)
                    is_existing = was_existing or get_last_video_result() == "existing"
                    if is_existing:
                        page_existing_count += 1
                    else:
                        page_new_count += 1
                        time.sleep(VIDEO_SUCCESS_PAUSE_SECONDS)
                else:
                    is_existing = False
                    time.sleep(CURRENT_SLEEP)
                if stats:
                    stats.mark_processed("videos", vid_id, log_visible=not is_existing)

            log_video_page_summary(page_new_count, page_existing_count)
            if stats:
                stats.complete_page("videos")
            if stats:
                stats.add_page_summary(
                    "videos",
                    page_new_count,
                    page_existing_count,
                )

    if control:
        control.wait_until_can_start()

    return finish_crawl()

# ================= MAIN =================

def main():
    global VIDEO_SUCCESS_PAUSE_SECONDS

    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("url", nargs="?")
    p.add_argument("--page", default=None)
    p.add_argument("--video-pause", type=float, default=VIDEO_SUCCESS_PAUSE_SECONDS)
    p.add_argument("--update", action="store_true")
    args = p.parse_args()

    if args.update:
        updated = run_update()
        sys.exit(0 if updated else 1)

    if not args.url:
        p.error("url es requerido salvo que uses --update")

    check_for_update_notice()

    selected_pages = None
    if args.page:
        try:
            selected_pages = [int(x.strip()) for x in args.page.split(",")]
        except ValueError:
            print("Error: usa --page 5 o --page 3,5,8")
            sys.exit(1)

    VIDEO_SUCCESS_PAUSE_SECONDS = max(0, args.video_pause)

    profile, typ, item_id = extract_profile_from_url(args.url)
    stats = TaskStats(
        single_page_task=is_single_manual_page_task(typ, item_id, selected_pages),
        page_progress_enabled=(
            typ in ("photo", "video")
            and item_id is None
            and selected_pages is not None
        ),
    )

    print(active_license_label())

    base = DOWNLOADS_ROOT
    os.makedirs(base, exist_ok=True)

    profile_folder = os.path.join(base, profile)
    os.makedirs(profile_folder, exist_ok=True)

    # =========================
    # FOTO INDIVIDUAL
    # =========================
    if typ == "photo" and item_id:
        process_single_photo_by_id(
            profile,
            item_id,
            profile_folder
        )

    # =========================
    # VIDEO INDIVIDUAL
    # =========================
    elif typ == "video" and item_id:
        process_single_video_by_id(
            profile,
            item_id,
            profile_folder
        )

    # =========================
    # TODAS LAS FOTOS
    # =========================
    elif typ == "photo":
        crawl_collection(
            profile,
            "photos",
            base,
            selected_pages,
            stats=stats
        )

    # =========================
    # TODOS LOS VIDEOS
    # =========================
    elif typ == "video":
        crawl_collection(
            profile,
            "videos",
            base,
            selected_pages,
            stats=stats
        )

    # =========================
    # PERFIL COMPLETO
    # =========================
    else:
        crawl_collection(
            profile,
            "photos",
            base,
            selected_pages,
            stats=stats
        )

        if IS_PREMIUM:
            crawl_collection(
                profile,
                "videos",
                base,
                selected_pages,
                stats=stats
            )
        else:
            log_info(profile_premium_message())

    log_task_summary(stats)


if __name__ == "__main__":
    main()
