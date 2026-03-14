#!/usr/bin/env python3
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import wave
from io import BytesIO
import json
import logging
from pathlib import Path
from queue import Empty, Queue
import re
import socket
import signal

import numpy as np
import pyperclip
import requests
import sounddevice as sd
from PyQt6.QtCore import QPoint, QRectF, QSettings, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QCursor, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "voiceClip"
ORGANIZATION_NAME = "voiceClip"
ORGANIZATION_DOMAIN = "com.voiceclip"
BUNDLE_ID = "com.voiceclip.voiceClip"
LAUNCH_AGENT_LABEL = "com.voiceclip.voiceclip.login"

SAMPLE_RATE = 16000
FAST_MODEL_NAME = "ggml-large-v3.bin"
HQ_MODEL_NAME = "ggml-large-v3.bin"

DEFAULT_FAST_MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin"
DEFAULT_HQ_MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin"

ENV_WHISPER_CLI = "VOICECLIP_WHISPER_CLI"
ENV_WHISPER_SERVER = "VOICECLIP_WHISPER_SERVER"
ENV_MODEL_PATH = "VOICECLIP_MODEL_PATH"  # legacy alias to HQ path
ENV_MODEL_URL = "VOICECLIP_MODEL_URL"  # legacy alias to HQ URL
ENV_FAST_MODEL_PATH = "VOICECLIP_FAST_MODEL_PATH"
ENV_FAST_MODEL_URL = "VOICECLIP_FAST_MODEL_URL"
ENV_HQ_MODEL_PATH = "VOICECLIP_HQ_MODEL_PATH"
ENV_HQ_MODEL_URL = "VOICECLIP_HQ_MODEL_URL"
ENV_CHUNK_MS = "VOICECLIP_CHUNK_MS"
ENV_OVERLAP_MS = "VOICECLIP_OVERLAP_MS"
ENV_SERVER_PORT = "VOICECLIP_SERVER_PORT"
ENV_ENABLE_VIBRANCY = "VOICECLIP_ENABLE_VIBRANCY"
ENV_SERVER_CLEANUP_MODE = "VOICECLIP_SERVER_CLEANUP_MODE"
ENV_MAX_QUEUE_CHUNKS = "VOICECLIP_MAX_QUEUE_CHUNKS"
ENV_ACTION_DEBOUNCE_MS = "VOICECLIP_ACTION_DEBOUNCE_MS"
ENV_STOPPING_TIMEOUT_SECONDS = "VOICECLIP_STOPPING_TIMEOUT_SECONDS"
ENV_RECORD_START_TIMEOUT_SECONDS = "VOICECLIP_RECORD_START_TIMEOUT_SECONDS"
ENV_REMOTE_SERVER_URL = "VOICECLIP_REMOTE_SERVER_URL"
ENV_REMOTE_API_KEY = "VOICECLIP_REMOTE_API_KEY"
ENV_GROQ_API_KEY = "GROQ_API_KEY"

INSTANCE_SERVER_NAME = "com.voiceclip.voiceclip.instance"

WIDGET_COMPACT_WIDTH = 160
WIDGET_COMPACT_HEIGHT = 92
WIDGET_COPY_HEIGHT = 138
WIDGET_DOWNLOAD_WIDTH = 260
WIDGET_DOWNLOAD_HEIGHT = 122

ACCENT_ORANGE = "#ff5a1f"
ACCENT_ORANGE_HOVER = "#ff7040"
ACCENT_ORANGE_DARK = "#e14b17"
ACCENT_ORANGE_SOFT = "#ffd9cc"
CHECK_FLASH_MS = 500

STREAM_CHUNK_MS_DEFAULT = 10000
STREAM_OVERLAP_MS_DEFAULT = 1000
STREAM_MIN_TAIL_MS = 220
STREAM_FLUSH_TIMEOUT_SECONDS = 45.0
MAX_QUEUE_CHUNKS_DEFAULT = 120
ACTION_DEBOUNCE_MS_DEFAULT = 200
SERVER_HEALTH_TIMEOUT_SECONDS = 60.0
MIC_ICON_SVG_NAME = "microphone.svg"
AUDIO_SHUTDOWN_STALE_SECONDS = 2.8
AUDIO_FORCE_RELEASE_LOG_THROTTLE_SECONDS = 5.0
MIC_START_TIMEOUT_SECONDS_DEFAULT = 3.5
AUDIO_WORKER_STOP_TIMEOUT_SECONDS = 1.6
MIC_START_RETRY_MAX = 2
MIC_START_RETRY_DELAY_MS = 250

STATE_BOOT = "boot"
STATE_DOWNLOADING = "downloading"
STATE_IDLE = "idle"
STATE_STARTING = "starting"
STATE_RECORDING = "recording"
STATE_STOPPING = "stopping"
STATE_PROCESSING = "processing"
STATE_CHECK = "check"
STATE_COPY_READY = "copy"
STATE_ERROR = "error"

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
SERVER_REGISTRY_PATH = APP_SUPPORT_DIR / "whisper_servers.json"
PID_FILE_PATH = APP_SUPPORT_DIR / "voiceclip.pid"
LOG_DIR = Path.home() / "Library" / "Logs" / APP_NAME
LOG_PATH = LOG_DIR / "voiceclip.log"


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("voiceclip")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    except Exception:
        logger.addHandler(logging.NullHandler())
    return logger


LOGGER = _build_logger()


def _load_env_local() -> None:
    candidates = [
        Path(__file__).resolve().parent / ".env.local",
        Path(sys.executable).resolve().parent / ".env.local",
        APP_SUPPORT_DIR / ".env.local",
    ]
    env_file = None
    for candidate in candidates:
        if candidate.is_file():
            env_file = candidate
            break
    if env_file is None:
        return
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key:
                os.environ.setdefault(key, value)
                LOGGER.info("env_local_loaded key=%s", key)
    except Exception as exc:
        LOGGER.warning("env_local_load_failed error=%s", exc)


_load_env_local()


def _worker_option_value(args: list[str], name: str) -> str | None:
    try:
        index = args.index(name)
    except ValueError:
        return None
    if index + 1 >= len(args):
        return None
    return args[index + 1]


def run_audio_worker_from_argv(args: list[str]) -> int | None:
    if "--audio-worker" not in args:
        return None

    wav_raw = _worker_option_value(args, "--wav-path")
    ready_raw = _worker_option_value(args, "--ready-path")
    status_raw = _worker_option_value(args, "--status-path")
    sample_rate_raw = _worker_option_value(args, "--sample-rate")

    if not wav_raw or not ready_raw or not status_raw or not sample_rate_raw:
        return 2

    wav_path = Path(wav_raw).expanduser()
    ready_path = Path(ready_raw).expanduser()
    status_path = Path(status_raw).expanduser()

    try:
        sample_rate = int(sample_rate_raw)
    except ValueError:
        return 2

    stop_event = threading.Event()

    def _signal_stop(signum, frame) -> None:  # type: ignore[no-untyped-def]
        del signum, frame
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _signal_stop)
        except Exception:
            pass

    def _write_status(message: str) -> None:
        try:
            status_path.parent.mkdir(parents=True, exist_ok=True)
            status_path.write_text(message, encoding="utf-8")
        except Exception:
            pass

    capture_rate = sample_rate
    input_device: int | None = None
    try:
        default_devices = sd.default.device
        if isinstance(default_devices, (list, tuple)) and len(default_devices) >= 1:
            candidate = int(default_devices[0])
            if candidate >= 0:
                input_device = candidate
    except Exception:
        input_device = None

    if input_device is not None:
        try:
            info = sd.query_devices(input_device, kind="input")
            default_rate = float(info.get("default_samplerate") or 0.0)
            if default_rate >= 8000:
                capture_rate = int(round(default_rate))
        except Exception:
            capture_rate = sample_rate

    try:
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        ready_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(wav_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(capture_rate)

            stream_kwargs: dict[str, object] = {
                "samplerate": capture_rate,
                "channels": 1,
                "dtype": "int16",
            }
            if input_device is not None:
                stream_kwargs["device"] = input_device

            with sd.InputStream(**stream_kwargs) as stream:
                ready_path.write_text("READY", encoding="utf-8")
                while not stop_event.is_set():
                    data, _overflow = stream.read(1024)
                    if data is None:
                        continue
                    chunk = np.asarray(data, dtype=np.int16).reshape(-1)
                    if chunk.size == 0:
                        continue
                    wav_file.writeframes(chunk.tobytes())

        _write_status("OK")
        return 0
    except Exception as exc:
        _write_status(str(exc))
        return 1


def app_bundle_path() -> Path | None:
    executable = Path(sys.executable).resolve()
    if ".app/Contents/MacOS" in str(executable):
        return executable.parents[2]
    return None


def resolve_asset_path(file_name: str) -> Path | None:
    candidates: list[Path] = []

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(str(meipass)) / "assets" / file_name)

    base_dir = Path(__file__).resolve().parent
    candidates.append(base_dir / "assets" / file_name)

    bundle = app_bundle_path()
    if bundle:
        candidates.append(bundle / "Contents" / "Resources" / "assets" / file_name)
        candidates.append(bundle / "Contents" / "MacOS" / "assets" / file_name)

    for path in candidates:
        if path.is_file():
            return path
    return None


def tinted_svg_icon_pixmap(file_name: str, size: int, color: QColor, dpr: float = 1.0) -> QPixmap | None:
    asset_path = resolve_asset_path(file_name)
    if not asset_path:
        return None

    dpr = max(1.0, float(dpr))
    pixel_size = max(1, int(round(size * dpr)))
    renderer = QSvgRenderer(str(asset_path))
    if not renderer.isValid():
        return None
    result = QPixmap(pixel_size, pixel_size)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, pixel_size, pixel_size))
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(result.rect(), color)
    painter.end()
    result.setDevicePixelRatio(dpr)
    return result


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def login_item_enabled() -> bool:
    return launch_agent_path().is_file()


def set_login_item_enabled(enabled: bool) -> None:
    plist_path = launch_agent_path()
    uid = str(os.getuid())

    if enabled:
        plist_path.parent.mkdir(parents=True, exist_ok=True)

        bundle_path = app_bundle_path()
        if bundle_path:
            program_arguments = ["/usr/bin/open", "-a", str(bundle_path)]
            working_directory = str(bundle_path)
        else:
            program_arguments = [sys.executable, str(Path(__file__).resolve())]
            working_directory = str(Path(__file__).resolve().parent)

        plist_data = {
            "Label": LAUNCH_AGENT_LABEL,
            "ProgramArguments": program_arguments,
            "RunAtLoad": True,
            "WorkingDirectory": working_directory,
            "KeepAlive": False,
        }

        with plist_path.open("wb") as handle:
            plistlib.dump(plist_data, handle)

        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist_path)], check=False, capture_output=True)
        subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)], check=False, capture_output=True)
        return

    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist_path)], check=False, capture_output=True)
    if plist_path.exists():
        plist_path.unlink()


def bytes_to_human(byte_count: int) -> str:
    if byte_count <= 0:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(byte_count)
    for unit in units:
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def _env_int(name: str, default: int, minimum: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(minimum, parsed)


def _env_float(name: str, default: float, minimum: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return max(minimum, parsed)


def chunk_ms_setting() -> int:
    return _env_int(ENV_CHUNK_MS, STREAM_CHUNK_MS_DEFAULT, 600)


def overlap_ms_setting() -> int:
    return _env_int(ENV_OVERLAP_MS, STREAM_OVERLAP_MS_DEFAULT, 100)


def configured_server_port() -> int | None:
    value = os.environ.get(ENV_SERVER_PORT)
    if value is None or not value.strip():
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    if parsed < 1024 or parsed > 65535:
        return None
    return parsed


def max_queue_chunks_setting() -> int:
    return _env_int(ENV_MAX_QUEUE_CHUNKS, MAX_QUEUE_CHUNKS_DEFAULT, 8)


def action_debounce_ms_setting() -> int:
    return _env_int(ENV_ACTION_DEBOUNCE_MS, ACTION_DEBOUNCE_MS_DEFAULT, 80)


def stopping_timeout_seconds_setting() -> float:
    return _env_float(ENV_STOPPING_TIMEOUT_SECONDS, STREAM_FLUSH_TIMEOUT_SECONDS, 3.0)


def record_start_timeout_seconds_setting() -> float:
    return _env_float(ENV_RECORD_START_TIMEOUT_SECONDS, MIC_START_TIMEOUT_SECONDS_DEFAULT, 1.0)


def server_cleanup_mode_setting() -> str:
    mode = os.environ.get(ENV_SERVER_CLEANUP_MODE, "owned").strip().lower()
    if mode not in {"owned", "global", "off"}:
        return "owned"
    return mode


def fast_model_url() -> str:
    return os.environ.get(ENV_FAST_MODEL_URL, DEFAULT_FAST_MODEL_URL)


def hq_model_url() -> str:
    legacy = os.environ.get(ENV_MODEL_URL)
    if legacy:
        return legacy
    return os.environ.get(ENV_HQ_MODEL_URL, DEFAULT_HQ_MODEL_URL)


def vibrancy_enabled() -> bool:
    value = os.environ.get(ENV_ENABLE_VIBRANCY, "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def model_path_candidates(model_name: str, explicit_env: str | None, legacy_env: str | None = None) -> list[Path]:
    candidates: list[Path] = []

    explicit = os.environ.get(explicit_env) if explicit_env else None
    if explicit:
        candidates.append(Path(explicit).expanduser())

    if legacy_env:
        legacy = os.environ.get(legacy_env)
        if legacy:
            candidates.append(Path(legacy).expanduser())

    candidates.extend(
        [
            Path.home() / ".whisper" / model_name,
            Path.home() / ".cache" / "whisper" / model_name,
        ]
    )
    return candidates


def find_existing_model_path(
    model_name: str,
    explicit_env: str | None,
    *,
    legacy_env: str | None = None,
    minimum_bytes: int = 32 * 1024 * 1024,
) -> Path | None:
    for candidate in model_path_candidates(model_name, explicit_env, legacy_env):
        if candidate.is_file() and candidate.stat().st_size > minimum_bytes:
            return candidate
    return None


def default_model_path(model_name: str, explicit_env: str | None, legacy_env: str | None = None) -> Path:
    explicit = os.environ.get(explicit_env) if explicit_env else None
    if explicit:
        return Path(explicit).expanduser()

    if legacy_env:
        legacy = os.environ.get(legacy_env)
        if legacy:
            return Path(legacy).expanduser()

    return Path.home() / ".whisper" / model_name


def find_existing_fast_model_path() -> Path | None:
    return find_existing_model_path(FAST_MODEL_NAME, ENV_FAST_MODEL_PATH)


def find_existing_hq_model_path() -> Path | None:
    return find_existing_model_path(HQ_MODEL_NAME, ENV_HQ_MODEL_PATH, legacy_env=ENV_MODEL_PATH, minimum_bytes=900_000_000)


def default_fast_model_path() -> Path:
    return default_model_path(FAST_MODEL_NAME, ENV_FAST_MODEL_PATH)


def default_hq_model_path() -> Path:
    return default_model_path(HQ_MODEL_NAME, ENV_HQ_MODEL_PATH, legacy_env=ENV_MODEL_PATH)


def find_whisper_server() -> str | None:
    env_path = os.environ.get(ENV_WHISPER_SERVER)
    if env_path and Path(env_path).is_file():
        return env_path

    candidates = [
        Path("/opt/homebrew/bin/whisper-server"),
        Path("/usr/local/bin/whisper-server"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    path = shutil.which("whisper-server")
    if path:
        return path

    return None


def find_whisper_cli() -> str | None:
    env_path = os.environ.get(ENV_WHISPER_CLI)
    if env_path and Path(env_path).is_file():
        return env_path

    candidates = [
        Path("/opt/homebrew/bin/whisper-cli"),
        Path("/usr/local/bin/whisper-cli"),
    ]

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    path = shutil.which("whisper-cli")
    if path:
        return path

    return None


def reserve_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def terminate_pid(pid: int, timeout_s: float = 2.0) -> bool:
    if not pid_is_alive(pid):
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return True

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not pid_is_alive(pid):
            return True
        time.sleep(0.05)

    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return True

    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        if not pid_is_alive(pid):
            return True
        time.sleep(0.05)
    return not pid_is_alive(pid)


def load_server_registry() -> list[dict[str, object]]:
    try:
        if not SERVER_REGISTRY_PATH.is_file():
            return []
        payload = json.loads(SERVER_REGISTRY_PATH.read_text(encoding="utf-8"))
        entries = payload.get("entries", [])
        if isinstance(entries, list):
            return [entry for entry in entries if isinstance(entry, dict)]
    except Exception:
        pass
    return []


def save_server_registry(entries: list[dict[str, object]]) -> None:
    APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "entries": entries}
    tmp_path = SERVER_REGISTRY_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    tmp_path.replace(SERVER_REGISTRY_PATH)


def read_pid_file() -> int | None:
    try:
        raw = PID_FILE_PATH.read_text(encoding="utf-8").strip()
        if raw.isdigit():
            pid = int(raw)
            if pid > 0:
                return pid
    except Exception:
        pass
    return None


def write_pid_file(pid: int) -> None:
    try:
        APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
        PID_FILE_PATH.write_text(str(pid), encoding="utf-8")
    except Exception:
        return


def clear_pid_file(pid: int | None = None) -> None:
    try:
        if not PID_FILE_PATH.exists():
            return
        if pid is None:
            PID_FILE_PATH.unlink(missing_ok=True)
            return
        existing = read_pid_file()
        if existing == pid:
            PID_FILE_PATH.unlink(missing_ok=True)
    except Exception:
        return


def register_owned_server(pid: int, model_path: str, port: int) -> None:
    entries = [entry for entry in load_server_registry() if int(entry.get("pid", 0)) != pid]
    entries.append(
        {
            "owner": APP_NAME,
            "pid": pid,
            "port": port,
            "model": model_path,
            "started_at": int(time.time()),
        }
    )
    save_server_registry(entries)


def unregister_owned_server(pid: int) -> None:
    entries = [entry for entry in load_server_registry() if int(entry.get("pid", 0)) != pid]
    save_server_registry(entries)


def cleanup_owned_whisper_servers(mode: str) -> None:
    entries = load_server_registry()
    kept_entries: list[dict[str, object]] = []
    registered_pids: set[int] = set()

    for entry in entries:
        pid = int(entry.get("pid", 0))
        owner = str(entry.get("owner", "")).strip().lower()
        should_cleanup = False
        if mode == "global":
            should_cleanup = True
        elif mode == "owned":
            should_cleanup = owner == APP_NAME.lower()
        elif mode == "off":
            should_cleanup = False

        if should_cleanup and pid > 0 and pid_is_alive(pid):
            stopped = terminate_pid(pid)
            LOGGER.info("server_cleanup mode=%s pid=%s stopped=%s", mode, pid, stopped)
            if not stopped:
                kept_entries.append(entry)
                registered_pids.add(pid)
            continue

        if pid > 0 and pid_is_alive(pid):
            kept_entries.append(entry)
            registered_pids.add(pid)

    if mode in {"owned", "global"}:
        try:
            result = subprocess.run(["pgrep", "-f", "whisper-server"], capture_output=True, text=True, check=False)
            for raw in result.stdout.splitlines():
                raw = raw.strip()
                if not raw.isdigit():
                    continue
                pid = int(raw)
                if pid in registered_pids:
                    continue
                if mode == "global":
                    terminate_pid(pid)
                    LOGGER.info("server_cleanup_global pid=%s", pid)
                    continue

                # owned-mode migration cleanup:
                # kill legacy orphan servers from older voiceClip builds (fixed port 8178).
                cmdline = ""
                try:
                    ps = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, check=False)
                    cmdline = ps.stdout.strip()
                except Exception:
                    cmdline = ""
                is_legacy_voiceclip = (
                    "whisper-server" in cmdline
                    and "--port 8178" in cmdline
                    and "ggml-large-v3" in cmdline
                )
                if is_legacy_voiceclip:
                    terminate_pid(pid)
                    LOGGER.info("server_cleanup_owned_legacy pid=%s", pid)
        except Exception:
            pass

    save_server_registry(kept_entries)


def copy_text_to_clipboard(text: str) -> None:
    try:
        pyperclip.copy(text)
        return
    except Exception:
        pass

    subprocess.run(["pbcopy"], input=text, text=True, check=True)


def request_foreground_activation() -> None:
    if sys.platform != "darwin":
        return

    try:
        from AppKit import NSApplication

        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception:
        return


def install_macos_vibrancy(widget: QWidget) -> bool:
    if sys.platform != "darwin":
        return False

    try:
        from AppKit import (
            NSApp,
            NSViewHeightSizable,
            NSViewWidthSizable,
            NSVisualEffectBlendingModeBehindWindow,
            NSVisualEffectMaterialHUDWindow,
            NSVisualEffectStateActive,
            NSVisualEffectView,
            NSWindowBelow,
        )
    except Exception:
        return False

    try:
        widget.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        widget.winId()

        ns_window = NSApp.keyWindow()
        if ns_window is None:
            windows = NSApp.windows()
            if windows:
                ns_window = windows[-1]

        if ns_window is None:
            return False

        content_view = ns_window.contentView()
        if content_view is None:
            return False

        effect_view = NSVisualEffectView.alloc().initWithFrame_(content_view.bounds())
        effect_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        effect_view.setMaterial_(NSVisualEffectMaterialHUDWindow)
        effect_view.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        effect_view.setState_(NSVisualEffectStateActive)

        if hasattr(effect_view, "setEmphasized_"):
            effect_view.setEmphasized_(True)

        content_view.addSubview_positioned_relativeTo_(effect_view, NSWindowBelow, None)
        widget._ns_effect_view = effect_view  # type: ignore[attr-defined]
        return True
    except Exception:
        return False


def install_macos_overlay_behavior(widget: QWidget) -> bool:
    if sys.platform != "darwin":
        return False

    try:
        from AppKit import (
            NSApp,
            NSFloatingWindowLevel,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorMoveToActiveSpace,
        )
    except Exception:
        return False

    try:
        widget.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        widget.winId()

        ns_window = None
        for candidate in NSApp.windows():
            try:
                if str(candidate.title()) == APP_NAME:
                    ns_window = candidate
                    break
            except Exception:
                continue

        if ns_window is None:
            windows = NSApp.windows()
            if windows:
                ns_window = windows[-1]

        if ns_window is None:
            return False

        ns_window.setHidesOnDeactivate_(False)
        ns_window.setLevel_(NSFloatingWindowLevel)

        behavior = int(ns_window.collectionBehavior())
        behavior |= int(NSWindowCollectionBehaviorCanJoinAllSpaces)
        behavior |= int(NSWindowCollectionBehaviorMoveToActiveSpace)
        behavior |= int(NSWindowCollectionBehaviorFullScreenAuxiliary)
        ns_window.setCollectionBehavior_(behavior)

        widget._ns_overlay_window = ns_window  # type: ignore[attr-defined]
        return True
    except Exception:
        return False


class SingleInstanceGuard:
    def __init__(self, server_name: str) -> None:
        self.server_name = server_name
        self.server: QLocalServer | None = None
        self.on_raise_request = None

    def acquire_or_raise_existing(self) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(self.server_name)
        if socket.waitForConnected(120):
            socket.write(b"raise")
            socket.flush()
            socket.waitForBytesWritten(120)
            acknowledged = False
            if socket.waitForReadyRead(400):
                try:
                    payload = bytes(socket.readAll()).strip().lower()
                    acknowledged = payload == b"ok"
                except Exception:
                    acknowledged = False
            socket.disconnectFromServer()
            if acknowledged:
                return False

            # Existing instance is connected but not responsive enough.
            stale_pid = read_pid_file()
            if stale_pid and stale_pid != os.getpid() and pid_is_alive(stale_pid):
                terminate_pid(stale_pid)
                LOGGER.error("single_instance_takeover killed_stale_pid=%s", stale_pid)

        QLocalServer.removeServer(self.server_name)
        self.server = QLocalServer()
        if not self.server.listen(self.server_name):
            return False

        self.server.newConnection.connect(self._on_new_connection)
        return True

    def _on_new_connection(self) -> None:
        if not self.server:
            return

        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.waitForReadyRead(120)
            try:
                _ = bytes(socket.readAll())
            except Exception:
                pass
            try:
                socket.write(b"ok")
                socket.flush()
                socket.waitForBytesWritten(80)
            except Exception:
                pass
            socket.disconnectFromServer()

            if self.on_raise_request:
                self.on_raise_request()


def pcm16_to_wav_bytes(pcm_data: bytes, sample_rate: int) -> bytes:
    with BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_data)
        return buffer.getvalue()


def extract_whisper_server_text(response: requests.Response) -> str:
    content_type = response.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        payload = response.json()
        if isinstance(payload, dict):
            return str(payload.get("text", "")).strip()
        return ""
    return response.text.strip()


def _normalize_token(token: str) -> str:
    return re.sub(r"[^0-9A-Za-zÄÖÜäöüß]+", "", token).lower()


def merge_transcript_chunks(chunks: list[str]) -> str:
    merged_tokens: list[str] = []
    for chunk in chunks:
        tokens = [token for token in chunk.split() if token]
        if not tokens:
            continue
        if not merged_tokens:
            merged_tokens.extend(tokens)
            continue

        overlap = 0
        max_overlap = min(36, len(merged_tokens), len(tokens))
        for size in range(max_overlap, 0, -1):
            left = merged_tokens[-size:]
            right = tokens[:size]
            if all(_normalize_token(a) == _normalize_token(b) for a, b in zip(left, right)):
                overlap = size
                break
        merged_tokens.extend(tokens[overlap:])

    return " ".join(merged_tokens).strip()


class WhisperServerProcessManager:
    def __init__(self, model_path: str, *, host: str = "127.0.0.1", port: int | None = None) -> None:
        self.model_path = model_path
        self.host = host
        self._configured_port = port if port is not None else configured_server_port()
        self.port = self._configured_port or reserve_free_port(host)
        self._process: subprocess.Popen[str] | None = None
        self._owned_pid: int | None = None
        self._lock = threading.Lock()

    @staticmethod
    def cleanup_registered_servers() -> None:
        mode = server_cleanup_mode_setting()
        cleanup_owned_whisper_servers(mode)

    @property
    def inference_url(self) -> str:
        return f"http://{self.host}:{self.port}/inference"

    @property
    def health_url(self) -> str:
        return f"http://{self.host}:{self.port}/health"

    def _process_is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _health_check(self) -> bool:
        try:
            response = requests.get(self.health_url, timeout=0.75)
            return response.status_code == 200
        except Exception:
            return False

    def is_healthy(self) -> bool:
        return self._process_is_alive() and self._health_check()

    def ensure_running(self) -> None:
        with self._lock:
            if self._process_is_alive() and self._health_check():
                return

            self._stop_locked()
            whisper_server = find_whisper_server()
            if not whisper_server:
                raise RuntimeError("whisper-server nicht gefunden. Installiere es mit: brew install whisper-cpp")

            self.port = self._configured_port if self._configured_port is not None else reserve_free_port(self.host)

            command = [
                whisper_server,
                "-m",
                self.model_path,
                "--host",
                self.host,
                "--port",
                str(self.port),
                "-l",
                "de",
                "-nt",
                "-fa",
                "-t",
                str(max(4, int(os.cpu_count() or 8))),
                "-bo",
                "5",
                "-bs",
                "5",
            ]

            self._process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._owned_pid = int(self._process.pid)
            register_owned_server(self._owned_pid, self.model_path, self.port)
            LOGGER.info("server_start pid=%s port=%s model=%s", self._owned_pid, self.port, self.model_path)

        deadline = time.monotonic() + SERVER_HEALTH_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self._health_check():
                self.warmup()
                return
            if not self._process_is_alive():
                details = ""
                failed_pid = self._owned_pid
                if self._process and self._process.stderr:
                    try:
                        details = self._process.stderr.read().strip()
                    except Exception:
                        details = ""
                if self._owned_pid:
                    unregister_owned_server(self._owned_pid)
                    self._owned_pid = None
                LOGGER.error("server_exit_early pid=%s details=%s", failed_pid, details[:220])
                raise RuntimeError(f"whisper-server beendet sich sofort. {details[:220]}")
            time.sleep(0.25)

        LOGGER.error("server_health_timeout pid=%s port=%s", self._owned_pid, self.port)
        raise RuntimeError("whisper-server konnte nicht gestartet werden (Healthcheck Timeout).")

    def warmup(self) -> None:
        silent_pcm = np.zeros(int(SAMPLE_RATE * 0.35), dtype=np.int16).tobytes()
        wav_data = pcm16_to_wav_bytes(silent_pcm, SAMPLE_RATE)
        try:
            with requests.Session() as session:
                response = session.post(
                    self.inference_url,
                    files={"file": ("warmup.wav", wav_data, "audio/wav")},
                    data={"response_format": "json", "temperature": "0.0", "temperature_inc": "0.0"},
                    timeout=8.0,
                )
                response.raise_for_status()
        except Exception:
            # Warmup ist best effort - das eigentliche Processing darf trotzdem starten.
            return

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        if not self._process:
            if self._owned_pid:
                unregister_owned_server(self._owned_pid)
                self._owned_pid = None
            return
        process = self._process
        self._process = None
        pid = int(process.pid)

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)
        unregister_owned_server(pid)
        self._owned_pid = None
        LOGGER.info("server_stop pid=%s", pid)


class TranscriptAssembler:
    def __init__(self) -> None:
        self._segments: dict[int, str] = {}
        self._lock = threading.Lock()

    def add(self, chunk_index: int, text: str) -> None:
        cleaned = text.strip()
        if not cleaned:
            return
        with self._lock:
            self._segments[chunk_index] = cleaned

    def merged_text(self) -> str:
        with self._lock:
            ordered = [self._segments[index] for index in sorted(self._segments.keys())]
        return merge_transcript_chunks(ordered)

    def is_empty(self) -> bool:
        with self._lock:
            return not self._segments


class StreamingTranscriptionController:
    def __init__(
        self,
        inference_url: str,
        *,
        sample_rate: int = SAMPLE_RATE,
        chunk_ms: int,
        overlap_ms: int,
        max_queue_chunks: int,
    ) -> None:
        self.inference_url = inference_url
        self.sample_rate = sample_rate
        self.chunk_samples = int(sample_rate * chunk_ms / 1000)
        self.overlap_samples = int(sample_rate * overlap_ms / 1000)
        if self.overlap_samples >= self.chunk_samples:
            self.overlap_samples = max(0, self.chunk_samples // 4)
        self.step_samples = max(1, self.chunk_samples - self.overlap_samples)
        self.min_tail_samples = int(sample_rate * STREAM_MIN_TAIL_MS / 1000)
        self.max_queue_chunks = max(8, max_queue_chunks)

        self._audio_pcm = bytearray()
        self._buffer_start_sample = 0
        self._total_samples = 0
        self._next_chunk_start = 0
        self._next_chunk_index = 0
        self._lock = threading.Lock()

        self._queue: Queue[tuple[int, bytes] | None] = Queue()
        self._done_event = threading.Event()
        self._abort_event = threading.Event()
        self._assembler = TranscriptAssembler()
        self._worker_error: str | None = None
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    def add_audio_samples(self, samples: np.ndarray) -> None:
        if self._abort_event.is_set():
            return

        flattened = np.asarray(samples, dtype=np.int16).reshape(-1)
        if flattened.size == 0:
            return

        pcm = flattened.tobytes()
        with self._lock:
            self._audio_pcm.extend(pcm)
            self._total_samples += flattened.size

        self._enqueue_ready_chunks()

    def finalize(self, wait_timeout_s: float) -> tuple[str, bool, str | None]:
        self._enqueue_final_tail()
        self._queue.put(None)

        finished = self._done_event.wait(wait_timeout_s)
        if not finished:
            self._abort_event.set()
            self._queue.put(None)
            self._done_event.wait(1.0)
            if self._worker_error is None:
                self._worker_error = "PROCESSING_TIMEOUT"

        transcript = self._assembler.merged_text()
        return transcript, finished, self._worker_error

    def cancel(self) -> None:
        self._abort_event.set()
        self._queue.put(None)
        self._done_event.wait(1.0)

    def queue_depth(self) -> int:
        try:
            return int(self._queue.qsize())
        except Exception:
            return 0

    def _enqueue_ready_chunks(self) -> None:
        while True:
            with self._lock:
                available = self._total_samples - self._next_chunk_start
                if available < self.chunk_samples:
                    return

                start = self._next_chunk_start
                end = start + self.chunk_samples
                chunk_index = self._next_chunk_index
                relative_start = max(0, start - self._buffer_start_sample)
                relative_end = max(0, end - self._buffer_start_sample)
                pcm_chunk = bytes(self._audio_pcm[relative_start * 2 : relative_end * 2])
                self._next_chunk_start += self.step_samples
                self._next_chunk_index += 1
                self._compact_audio_locked()

            if not self._enqueue_chunk(chunk_index, pcm_chunk):
                return

    def _enqueue_final_tail(self) -> None:
        with self._lock:
            remaining = self._total_samples - self._next_chunk_start
            should_enqueue_tail = (
                remaining >= self.min_tail_samples
                or (self._next_chunk_index == 0 and self._total_samples >= int(self.sample_rate * 0.20))
            )

            if not should_enqueue_tail:
                return

            start = self._next_chunk_start
            end = self._total_samples
            chunk_index = self._next_chunk_index
            relative_start = max(0, start - self._buffer_start_sample)
            relative_end = max(0, end - self._buffer_start_sample)
            pcm_chunk = bytes(self._audio_pcm[relative_start * 2 : relative_end * 2])
            self._next_chunk_index += 1
            self._next_chunk_start = self._total_samples
            self._compact_audio_locked()

        self._enqueue_chunk(chunk_index, pcm_chunk)

    def _compact_audio_locked(self) -> None:
        drop_samples = self._next_chunk_start - self._buffer_start_sample
        if drop_samples <= 0:
            return
        byte_count = min(len(self._audio_pcm), drop_samples * 2)
        if byte_count <= 0:
            return
        del self._audio_pcm[:byte_count]
        self._buffer_start_sample += byte_count // 2

    def _enqueue_chunk(self, chunk_index: int, pcm_chunk: bytes) -> bool:
        if self._abort_event.is_set():
            return False
        queue_depth = self.queue_depth()
        if queue_depth >= self.max_queue_chunks:
            if self._worker_error is None:
                self._worker_error = f"Chunk-Queue ueberlaufen ({queue_depth}/{self.max_queue_chunks})."
            self._abort_event.set()
            self._queue.put(None)
            return False
        self._queue.put((chunk_index, pcm_chunk))
        return True

    def _worker_loop(self) -> None:
        session = requests.Session()
        try:
            while not self._abort_event.is_set():
                try:
                    item = self._queue.get(timeout=0.2)
                except Empty:
                    continue

                if item is None:
                    break

                chunk_index, pcm_data = item
                try:
                    wav_data = pcm16_to_wav_bytes(pcm_data, self.sample_rate)
                    response = session.post(
                        self.inference_url,
                        files={"file": ("chunk.wav", wav_data, "audio/wav")},
                        data={
                            "response_format": "json",
                            "temperature": "0.0",
                            "temperature_inc": "0.0",
                        },
                        timeout=10.0,
                    )
                    response.raise_for_status()
                    text = extract_whisper_server_text(response)
                    self._assembler.add(chunk_index, text)
                except Exception as exc:
                    if self._worker_error is None:
                        self._worker_error = f"Chunk-Transkription fehlgeschlagen: {exc}"
                        LOGGER.error("stream_chunk_error chunk=%s error=%s", chunk_index, exc)
        finally:
            session.close()
            self._done_event.set()


class AudioRecorder:
    def __init__(self, sample_rate: int = SAMPLE_RATE) -> None:
        self.sample_rate = sample_rate
        self._capture_full_chunks = True
        self._worker_proc: subprocess.Popen | None = None
        self._worker_wav_path: Path | None = None
        self._worker_ready_path: Path | None = None
        self._worker_status_path: Path | None = None
        # In-process streaming state (used when capture_full_chunks=False)
        self._inprocess_stream: Any = None
        self._inprocess_lock = threading.Lock()
        self._inprocess_pending = bytearray()
        self._inprocess_full = bytearray()
        self._inprocess_active = False

    def is_active(self) -> bool:
        if self._inprocess_active:
            return True
        proc = self._worker_proc
        if proc is None:
            return False
        if proc.poll() is None:
            return True
        self._worker_proc = None
        return False

    def has_pending_shutdown(self) -> bool:
        return False

    def _new_worker_temp_path(self, suffix: str) -> Path:
        return Path(tempfile.gettempdir()) / f"voiceclip-worker-{uuid.uuid4().hex}{suffix}"

    def _cleanup_worker_files(self, *, remove_wav: bool) -> None:
        for path in (self._worker_ready_path, self._worker_status_path):
            if path:
                path.unlink(missing_ok=True)
        if remove_wav and self._worker_wav_path:
            self._worker_wav_path.unlink(missing_ok=True)

    def _read_worker_status(self) -> str:
        path = self._worker_status_path
        if not path or not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            return ""

    def _worker_command(self, wav_path: Path, ready_path: Path, status_path: Path) -> list[str]:
        command = [sys.executable]
        executable_name = Path(sys.executable).name.lower()
        if executable_name.startswith("python"):
            command.append(str(Path(__file__).resolve()))
        command.extend(
            [
                "--audio-worker",
                "--wav-path",
                str(wav_path),
                "--ready-path",
                str(ready_path),
                "--status-path",
                str(status_path),
                "--sample-rate",
                str(self.sample_rate),
            ]
        )
        return command

    def _terminate_worker_process(self, proc: subprocess.Popen, *, timeout_s: float) -> bool:
        if proc.poll() is not None:
            return True
        try:
            proc.send_signal(signal.SIGINT)
        except Exception:
            pass
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return True
            time.sleep(0.05)
        try:
            proc.terminate()
        except Exception:
            pass
        deadline = time.monotonic() + 0.6
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return True
            time.sleep(0.05)
        try:
            proc.kill()
        except Exception:
            pass
        return proc.poll() is not None

    def _resample_to_target(self, audio: np.ndarray, source_rate: int) -> np.ndarray:
        if audio.size == 0:
            return audio
        if source_rate <= 0 or source_rate == self.sample_rate:
            return audio
        target_len = int(round(audio.size * (self.sample_rate / float(source_rate))))
        if target_len <= 0:
            return np.empty(0, dtype=np.int16)
        source_x = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
        target_x = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
        resampled = np.interp(target_x, source_x, audio.astype(np.float32))
        return np.clip(np.round(resampled), -32768, 32767).astype(np.int16)

    def _start_inprocess(self) -> None:
        """Start in-process audio capture for streaming (no subprocess)."""
        import sounddevice as sd

        self._inprocess_pending = bytearray()
        self._inprocess_full = bytearray()
        self._inprocess_active = True

        def callback(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
            if not self._inprocess_active:
                return
            pcm = indata.tobytes()
            with self._inprocess_lock:
                self._inprocess_pending.extend(pcm)
            self._inprocess_full.extend(pcm)

        self._inprocess_stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            callback=callback,
            blocksize=self.sample_rate // 10,
        )
        self._inprocess_stream.start()

    def _stop_inprocess(self, *, require_full_chunks: bool = True) -> list[np.ndarray]:
        """Stop in-process audio capture."""
        self._inprocess_active = False
        if self._inprocess_stream:
            try:
                self._inprocess_stream.stop()
                self._inprocess_stream.close()
            except Exception:
                pass
            self._inprocess_stream = None

        if not self._capture_full_chunks:
            return []

        with self._inprocess_lock:
            audio = np.frombuffer(bytes(self._inprocess_full), dtype=np.int16).copy()
            self._inprocess_full = bytearray()

        total_samples = int(audio.size)
        if require_full_chunks and total_samples < int(self.sample_rate * 0.2):
            raise RuntimeError("Keine Audiodaten erfasst.")
        return [audio]

    def start(self, *, capture_full_chunks: bool = True) -> None:
        if self.is_active():
            raise RuntimeError("Recording laeuft bereits.")

        self._capture_full_chunks = capture_full_chunks

        if not capture_full_chunks:
            self._start_inprocess()
            return

        self._worker_wav_path = self._new_worker_temp_path(".wav")
        self._worker_ready_path = self._new_worker_temp_path(".ready")
        self._worker_status_path = self._new_worker_temp_path(".status")
        self._cleanup_worker_files(remove_wav=True)

        command = self._worker_command(self._worker_wav_path, self._worker_ready_path, self._worker_status_path)
        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            self._cleanup_worker_files(remove_wav=True)
            raise RuntimeError(f"Audio-Worker konnte nicht gestartet werden: {exc}") from exc

        self._worker_proc = proc
        ready_timeout = record_start_timeout_seconds_setting()
        deadline = time.monotonic() + ready_timeout
        while time.monotonic() < deadline:
            if self._worker_ready_path.is_file():
                return
            if proc.poll() is not None:
                error_message = self._read_worker_status() or f"Audio-Worker beendet (Code {proc.returncode})."
                self._worker_proc = None
                self._cleanup_worker_files(remove_wav=True)
                raise RuntimeError(error_message)
            time.sleep(0.05)

        self._terminate_worker_process(proc, timeout_s=0.6)
        self._worker_proc = None
        error_message = self._read_worker_status() or "Mikrofonstart reagiert nicht."
        self._cleanup_worker_files(remove_wav=True)
        raise RuntimeError(error_message)

    def consume_pending_samples(self) -> np.ndarray:
        with self._inprocess_lock:
            if not self._inprocess_pending:
                return np.empty(0, dtype=np.int16)
            data = np.frombuffer(bytes(self._inprocess_pending), dtype=np.int16).copy()
            self._inprocess_pending.clear()
            return data

    def stop(self, *, require_full_chunks: bool = True) -> list[np.ndarray]:
        if self._inprocess_active or self._inprocess_stream:
            return self._stop_inprocess(require_full_chunks=require_full_chunks)

        proc = self._worker_proc
        wav_path = self._worker_wav_path
        if proc is None or wav_path is None:
            raise RuntimeError("Keine aktive Aufnahme.")

        self._worker_proc = None
        self._terminate_worker_process(proc, timeout_s=AUDIO_WORKER_STOP_TIMEOUT_SECONDS)

        audio = np.empty(0, dtype=np.int16)
        source_rate = self.sample_rate
        try:
            with wave.open(str(wav_path), "rb") as wav_file:
                source_rate = int(wav_file.getframerate() or self.sample_rate)
                raw = wav_file.readframes(wav_file.getnframes())
                if raw:
                    audio = np.frombuffer(raw, dtype=np.int16).copy()
        except Exception as exc:
            self._cleanup_worker_files(remove_wav=True)
            raise RuntimeError(f"Audio konnte nicht gelesen werden: {exc}") from exc

        self._cleanup_worker_files(remove_wav=True)
        audio = self._resample_to_target(audio, source_rate)
        total_samples = int(audio.size)
        if require_full_chunks and total_samples < int(self.sample_rate * 0.2):
            raise RuntimeError("Keine Audiodaten erfasst.")
        if not require_full_chunks and total_samples < int(self.sample_rate * 0.2):
            raise RuntimeError("Aufnahme war zu kurz.")
        if not self._capture_full_chunks:
            return []
        return [audio]

    def force_release(self) -> None:
        if self._inprocess_stream:
            self._inprocess_active = False
            try:
                self._inprocess_stream.stop()
                self._inprocess_stream.close()
            except Exception:
                pass
            self._inprocess_stream = None
        with self._inprocess_lock:
            self._inprocess_pending = bytearray()
        self._inprocess_full = bytearray()

        proc = self._worker_proc
        if proc is None:
            return
        if proc.poll() is None:
            LOGGER.info("audio_force_release_worker pid=%s", proc.pid)
            self._terminate_worker_process(proc, timeout_s=0.5)
        self._worker_proc = None
        self._cleanup_worker_files(remove_wav=True)


class FinalizeRecordingThread(QThread):
    finished_ok = pyqtSignal(str, str)
    failed = pyqtSignal(str, str)

    def __init__(self, session_id: str, chunks: list[np.ndarray], sample_rate: int) -> None:
        super().__init__()
        self.session_id = session_id
        self.chunks = chunks
        self.sample_rate = sample_rate

    def run(self) -> None:
        try:
            audio = np.concatenate(self.chunks, axis=0).reshape(-1)
            if audio.size < int(self.sample_rate * 0.2):
                self.failed.emit(self.session_id, "Aufnahme war zu kurz.")
                return

            wav_path = Path(tempfile.gettempdir()) / f"voiceclip-{uuid.uuid4().hex}.wav"
            with wave.open(str(wav_path), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(self.sample_rate)
                wav_file.writeframes(audio.tobytes())

            self.finished_ok.emit(self.session_id, str(wav_path))
        except Exception as exc:
            self.failed.emit(self.session_id, f"Audiofinalisierung fehlgeschlagen: {exc}")


class ModelDownloadThread(QThread):
    progress = pyqtSignal(str, int, str)
    finished_ok = pyqtSignal(str, str)
    failed = pyqtSignal(str, str)

    def __init__(
        self,
        request_id: str,
        model_label: str,
        destination: Path,
        url: str,
        minimum_bytes: int = 32 * 1024 * 1024,
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.model_label = model_label
        self.destination = destination
        self.url = url
        self.minimum_bytes = minimum_bytes

    def run(self) -> None:
        try:
            if self.destination.is_file() and self.destination.stat().st_size > self.minimum_bytes:
                self.finished_ok.emit(self.request_id, str(self.destination))
                return

            self.destination.parent.mkdir(parents=True, exist_ok=True)
            temp_file = self.destination.with_suffix(".part")

            with requests.get(self.url, stream=True, timeout=30) as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length", "0"))
                downloaded = 0

                with temp_file.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if self.isInterruptionRequested():
                            raise RuntimeError("Download abgebrochen.")
                        if not chunk:
                            continue

                        handle.write(chunk)
                        downloaded += len(chunk)
                        percentage = int((downloaded * 100) / total) if total else 0
                        self.progress.emit(
                            self.request_id,
                            min(100, percentage),
                            f"{bytes_to_human(downloaded)} / {bytes_to_human(total)}",
                        )

            temp_file.replace(self.destination)
            self.progress.emit(self.request_id, 100, f"{self.model_label} bereit")
            self.finished_ok.emit(self.request_id, str(self.destination))
        except Exception as exc:
            self.destination.with_suffix(".part").unlink(missing_ok=True)
            self.failed.emit(self.request_id, str(exc))


class ServerWarmupThread(QThread):
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str, str)

    def __init__(self, manager: WhisperServerProcessManager, request_id: str) -> None:
        super().__init__()
        self.manager = manager
        self.request_id = request_id

    def run(self) -> None:
        try:
            self.manager.ensure_running()
            self.finished_ok.emit(self.request_id)
        except Exception as exc:
            self.failed.emit(self.request_id, str(exc))


class StreamFinalizeThread(QThread):
    finished_ok = pyqtSignal(str, str, bool, float)
    failed = pyqtSignal(str, str)

    def __init__(self, session_id: str, controller: StreamingTranscriptionController, stop_clicked_at: float, timeout_s: float) -> None:
        super().__init__()
        self.session_id = session_id
        self.controller = controller
        self.stop_clicked_at = stop_clicked_at
        self.timeout_s = timeout_s

    def run(self) -> None:
        transcript, finished, worker_error = self.controller.finalize(self.timeout_s)
        latency_ms = (time.monotonic() - self.stop_clicked_at) * 1000.0

        if not transcript:
            if worker_error:
                self.failed.emit(self.session_id, worker_error)
                return
            self.failed.emit(self.session_id, "Transkription war leer.")
            return

        if worker_error and not finished:
            # Bei Timeout liefern wir den bis dahin vorhandenen Teiltext aus.
            self.finished_ok.emit(self.session_id, transcript, False, latency_ms)
            return

        self.finished_ok.emit(self.session_id, transcript, finished, latency_ms)


class TranscribeThread(QThread):
    finished_ok = pyqtSignal(str, str)
    failed = pyqtSignal(str, str)

    def __init__(self, session_id: str, model_path: str, wav_path: Path) -> None:
        super().__init__()
        self.session_id = session_id
        self.model_path = model_path
        self.wav_path = wav_path

    def run(self) -> None:
        whisper_cli = find_whisper_cli()
        if not whisper_cli:
            self.failed.emit(self.session_id, "whisper-cli nicht gefunden. Installiere es mit: brew install whisper-cpp")
            return

        thread_count = max(4, int(os.cpu_count() or 8))
        output_base = Path(tempfile.gettempdir()) / f"voiceclip-transcript-{uuid.uuid4().hex}"
        output_txt = output_base.with_suffix(".txt")

        command = [
            whisper_cli,
            "-m",
            self.model_path,
            "-f",
            str(self.wav_path),
            "-l",
            "auto",
            "-otxt",
            "-of",
            str(output_base),
            "-np",
            "-nt",
            "-fa",
            "-t",
            str(thread_count),
        ]

        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            error_message = result.stderr.strip() or result.stdout.strip() or "Unbekannter whisper-cli Fehler"
            self.failed.emit(self.session_id, error_message)
            return

        transcript = ""
        if output_txt.exists():
            transcript = output_txt.read_text(encoding="utf-8", errors="ignore").strip()
            output_txt.unlink(missing_ok=True)

        if not transcript:
            transcript = result.stdout.strip()

        if not transcript:
            self.failed.emit(self.session_id, "Transkription war leer.")
            return

        self.finished_ok.emit(self.session_id, transcript)


def _remote_server_url() -> str:
    return os.environ.get(ENV_REMOTE_SERVER_URL, "").strip().rstrip("/")


def _remote_api_key() -> str:
    return os.environ.get(ENV_REMOTE_API_KEY, "").strip()


def _check_remote_server_health(url: str, api_key: str, timeout: float = 3.0) -> bool:
    if not url:
        return False
    try:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        response = requests.get(f"{url}/health", headers=headers, timeout=timeout)
        return response.status_code == 200
    except Exception:
        return False


class RemoteTranscribeThread(QThread):
    finished_ok = pyqtSignal(str, str)
    failed = pyqtSignal(str, str)

    def __init__(self, session_id: str, wav_path: Path, server_url: str, api_key: str) -> None:
        super().__init__()
        self.session_id = session_id
        self.wav_path = wav_path
        self.server_url = server_url.rstrip("/")
        self.api_key = api_key

    def run(self) -> None:
        url = f"{self.server_url}/transcribe"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            with open(self.wav_path, "rb") as f:
                response = requests.post(
                    url,
                    files={"file": ("recording.wav", f, "audio/wav")},
                    headers=headers,
                    timeout=180.0,
                )
            response.raise_for_status()
            data = response.json()
            transcript = data.get("text", "").strip()
            processing_ms = data.get("processing_ms", 0)
            LOGGER.info("remote_transcribe ok processing_ms=%s chars=%s", processing_ms, len(transcript))
            if not transcript:
                self.failed.emit(self.session_id, "Server hat leere Transkription zurueckgegeben.")
                return
            self.finished_ok.emit(self.session_id, transcript)
        except requests.exceptions.ConnectionError:
            self.failed.emit(self.session_id, "Server nicht erreichbar. Pruefe VOICECLIP_REMOTE_SERVER_URL.")
        except requests.exceptions.Timeout:
            self.failed.emit(self.session_id, "Server-Timeout bei Transkription.")
        except Exception as exc:
            self.failed.emit(self.session_id, f"Server-Fehler: {exc}")


def _groq_api_key() -> str:
    return os.environ.get(ENV_GROQ_API_KEY, "").strip()


class GroqTranscribeThread(QThread):
    finished_ok = pyqtSignal(str, str)
    failed = pyqtSignal(str, str)

    GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

    def __init__(self, session_id: str, wav_path: Path, api_key: str) -> None:
        super().__init__()
        self.session_id = session_id
        self.wav_path = wav_path
        self.api_key = api_key

    def run(self) -> None:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            with open(self.wav_path, "rb") as f:
                response = requests.post(
                    self.GROQ_URL,
                    files={"file": ("recording.wav", f, "audio/wav")},
                    data={"model": "whisper-large-v3", "language": "de"},
                    headers=headers,
                    timeout=60.0,
                )
            response.raise_for_status()
            data = response.json()
            transcript = data.get("text", "").strip()
            LOGGER.info("groq_transcribe ok chars=%s", len(transcript))
            if not transcript:
                self.failed.emit(self.session_id, "Groq API hat leere Transkription zurueckgegeben.")
                return
            self.finished_ok.emit(self.session_id, transcript)
        except requests.exceptions.ConnectionError:
            self.failed.emit(self.session_id, "Groq API nicht erreichbar. Pruefe Internetverbindung.")
        except requests.exceptions.Timeout:
            self.failed.emit(self.session_id, "Groq API Timeout.")
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            body = ""
            try:
                body = exc.response.json().get("error", {}).get("message", "") if exc.response is not None else ""
            except Exception:
                pass
            self.failed.emit(self.session_id, f"Groq API Fehler ({status}): {body or exc}")
        except Exception as exc:
            self.failed.emit(self.session_id, f"Groq Fehler: {exc}")


class VoiceClipWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.settings = QSettings()
        self.drag_offset: QPoint | None = None

        self.stream_chunk_ms = int(self.settings.value("stream.chunk_ms", chunk_ms_setting()))
        self.stream_overlap_ms = int(self.settings.value("stream.overlap_ms", overlap_ms_setting()))
        self.stream_chunk_ms = max(600, self.stream_chunk_ms)
        self.stream_overlap_ms = max(100, min(self.stream_overlap_ms, self.stream_chunk_ms - 100))
        self.settings.setValue("stream.chunk_ms", self.stream_chunk_ms)
        self.settings.setValue("stream.overlap_ms", self.stream_overlap_ms)

        self.recorder = AudioRecorder()
        self.fast_model_path: str | None = None
        self.hq_model_path: str | None = None
        # Groq API key (if set, Groq is primary transcription backend)
        self._groq_api_key = _groq_api_key()
        if self._groq_api_key:
            LOGGER.info("groq_api_configured")
            self.mode = "hq"
        else:
            self.mode = "hq"
        self.settings.setValue("mode.default", self.mode)

        self.server_manager: WhisperServerProcessManager | None = None
        self.stream_controller: StreamingTranscriptionController | None = None
        self.stream_started_at = 0.0
        self._start_recording_after_hq_download = False

        # Remote server configuration
        self._remote_server_url = _remote_server_url()
        self._remote_api_key = _remote_api_key()
        self._remote_server_healthy = False
        if self._remote_server_url:
            LOGGER.info("remote_server_configured url=%s", self._remote_server_url)
            self._remote_server_healthy = _check_remote_server_health(
                self._remote_server_url, self._remote_api_key
            )
        self.active_session_id: str | None = None
        self.last_transcript = ""
        self.current_wav_path: Path | None = None
        self.state = STATE_BOOT
        self._last_error_code = ""
        self.notify_callback = None
        self.state_callback = None

        self._max_queue_chunks = max_queue_chunks_setting()
        self._stopping_timeout_seconds = stopping_timeout_seconds_setting()
        self._record_start_timeout_seconds = record_start_timeout_seconds_setting()
        self._action_debounce_ms = action_debounce_ms_setting()
        self._action_in_flight = False
        self._last_action_at = 0.0
        self._record_start_result_queue: Queue[tuple[str, str | None]] = Queue()
        self._record_start_request_id: str | None = None
        self._record_start_session_id: str | None = None
        self._record_start_thread: threading.Thread | None = None
        self._record_start_deadline = 0.0
        self._record_start_retry_count = 0
        self._check_to_copy_timer = QTimer(self)
        self._check_to_copy_timer.setSingleShot(True)
        self._check_to_copy_timer.timeout.connect(self._transition_check_to_copy)
        self._check_failsafe_timer = QTimer(self)
        self._check_failsafe_timer.setSingleShot(True)
        self._check_failsafe_timer.timeout.connect(self._force_copy_if_still_check)
        self._record_start_timer = QTimer(self)
        self._record_start_timer.setInterval(60)
        self._record_start_timer.timeout.connect(self._poll_record_start)
        self._download_request_id: str | None = None
        self._warmup_request_id: str | None = None

        self.spinner_frames = ["·", "••", "•••", "••"]
        self.spinner_index = 0
        self.pulse_toggle = False

        self.spinner_timer = QTimer(self)
        self.spinner_timer.setInterval(150)
        self.spinner_timer.timeout.connect(self._spinner_tick)

        self.pulse_timer = QTimer(self)
        self.pulse_timer.setInterval(380)
        self.pulse_timer.timeout.connect(self._pulse_tick)

        self.stream_capture_timer = QTimer(self)
        self.stream_capture_timer.setInterval(120)
        self.stream_capture_timer.timeout.connect(self._on_stream_capture_tick)

        self.audio_guard_timer = QTimer(self)
        self.audio_guard_timer.setInterval(500)
        self.audio_guard_timer.timeout.connect(self._on_audio_guard_tick)
        self.audio_guard_timer.start()

        self.download_thread: ModelDownloadThread | None = None
        self.server_warmup_thread: ServerWarmupThread | None = None
        self.finalize_thread: FinalizeRecordingThread | None = None
        self.stream_finalize_thread: StreamFinalizeThread | None = None
        self.transcribe_thread: TranscribeThread | None = None

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(12, 12, 12, 12)
        self.main_layout.setSpacing(8)

        self.action_button = QPushButton()
        self.action_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action_button.setFixedSize(62, 62)
        self.action_button.clicked.connect(self._on_action_clicked)
        self.main_layout.addWidget(self.action_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.copy_button = QPushButton("Kopieren")
        self.copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_button.setFixedSize(126, 36)
        self.copy_button.clicked.connect(lambda: self.dispatch_primary_action(source="copy_button"))
        self.copy_button.hide()
        self.main_layout.addWidget(self.copy_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #dadada; font-size: 10px;")
        self.status_label.hide()
        self.main_layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(12)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.hide()
        self.progress_bar.setStyleSheet(
            """
            QProgressBar {
                border: 1px solid #2f2f2f;
                border-radius: 6px;
                background: rgba(0,0,0,130);
                color: #ececec;
                font-size: 10px;
                text-align: center;
            }
            QProgressBar::chunk {
                background: #ff5a1f;
                border-radius: 6px;
            }
            """
        )
        self.main_layout.addWidget(self.progress_bar)

        self.restore_position()
        self.enter_boot_state()
        QTimer.singleShot(0, self.ensure_model_available)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect().adjusted(0, 0, -1, -1)), 18, 18)
        painter.fillPath(path, QColor(15, 15, 15, 215))

        border_pen = QPen(QColor(255, 255, 255, 26), 1)
        painter.setPen(border_pen)
        painter.drawPath(path)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        event.ignore()
        self.hide()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        event.ignore()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self.drag_offset is None:
            return
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        del event
        self.drag_offset = None
        self.persist_position()

    def moveEvent(self, event) -> None:  # type: ignore[override]
        super().moveEvent(event)
        self.persist_position()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        QTimer.singleShot(10, self._install_overlay_behavior)
        QTimer.singleShot(20, self._install_blur_if_possible)

    def _install_overlay_behavior(self) -> None:
        self._overlay_installed = install_macos_overlay_behavior(self)

    def _install_blur_if_possible(self) -> None:
        if getattr(self, "_vibrancy_installed", False):
            return
        if not vibrancy_enabled():
            self._vibrancy_installed = False
            return
        self._vibrancy_installed = install_macos_vibrancy(self)

    def _show_compact(self) -> None:
        self.setFixedSize(WIDGET_COMPACT_WIDTH, WIDGET_COMPACT_HEIGHT)
        self.copy_button.hide()
        self.status_label.hide()
        self.progress_bar.hide()

    def _show_copy_layout(self) -> None:
        self.setFixedSize(WIDGET_COMPACT_WIDTH, WIDGET_COPY_HEIGHT)
        self.copy_button.show()
        self.copy_button.setStyleSheet(
            """
            QPushButton {
                background-color: #ff5a1f;
                color: #ffffff;
                border: none;
                border-radius: 18px;
                padding: 7px 12px;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #ff7040;
            }
            """
        )
        self.status_label.hide()
        self.progress_bar.hide()

    def _show_download_layout(self) -> None:
        self.setFixedSize(WIDGET_DOWNLOAD_WIDTH, WIDGET_DOWNLOAD_HEIGHT)
        self.copy_button.hide()
        self.status_label.show()
        self.progress_bar.show()

    def _build_mic_icon(self, mode: str) -> QIcon:
        size = 30
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if mode == "stop":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#ffffff"))
            painter.drawRoundedRect(8, 8, 14, 14, 2, 2)
        elif mode == "copy":
            pen = QPen(QColor("#f4f4f4"), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(7, 8, 13, 15, 3, 3)
            painter.drawRoundedRect(11, 4, 12, 14, 3, 3)
        elif mode == "check":
            pen = QPen(QColor("#e9ecef"), 2.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawLine(7, 16, 13, 22)
            painter.drawLine(13, 22, 24, 8)
        else:
            svg_pixmap = tinted_svg_icon_pixmap(MIC_ICON_SVG_NAME, size, QColor("#f8f9fa"))
            if svg_pixmap and not svg_pixmap.isNull():
                painter.drawPixmap(0, 0, svg_pixmap)
            else:
                # Fallback if svg asset is unavailable.
                fill = QColor("#f8f9fa")
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(fill)
                painter.drawRoundedRect(11, 4, 8, 13, 4, 4)

                pen = QPen(fill, 2.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawArc(8, 12, 14, 10, 180 * 16, -180 * 16)
                painter.drawLine(15, 19, 15, 23)
                painter.drawLine(12, 23, 18, 23)

        painter.end()
        return QIcon(pixmap)

    def _set_action_style(self, bg: str, hover: str, border_color: str = "transparent", border_width: int = 0) -> None:
        self.action_button.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {bg};
                border: {border_width}px solid {border_color};
                border-radius: 28px;
                color: #ffffff;
                font-size: 15px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background-color: {hover};
            }}
            QPushButton:disabled {{
                background-color: {bg};
                color: #ffffff;
            }}
            """
        )

    def _stop_animations(self) -> None:
        self.spinner_timer.stop()
        self.pulse_timer.stop()
        self._check_to_copy_timer.stop()
        self._check_failsafe_timer.stop()
        self.action_button.setText("")

    def _publish_state(self) -> None:
        self.settings.setValue("diagnostics.last_state", self.state)
        if self.state_callback:
            self.state_callback(self.state)

    def _set_state(self, next_state: str, *, reason: str = "") -> None:
        previous = self.state
        self.state = next_state
        LOGGER.info(
            "state_transition from=%s to=%s reason=%s session=%s",
            previous,
            next_state,
            reason,
            self.active_session_id or "-",
        )
        self._publish_state()

    def _set_last_error(self, code: str, message: str) -> None:
        self._last_error_code = code
        self.settings.setValue("diagnostics.last_error_code", code)
        self.settings.setValue("diagnostics.last_error_message", message)
        LOGGER.error("error code=%s state=%s session=%s message=%s", code, self.state, self.active_session_id or "-", message)

    def _start_session(self) -> str:
        session_id = uuid.uuid4().hex
        self.active_session_id = session_id
        LOGGER.info("session_start id=%s mode=%s", session_id, self.mode)
        return session_id

    def _is_session_active(self, session_id: str | None) -> bool:
        return bool(session_id and self.active_session_id and session_id == self.active_session_id)

    def _close_session(self, *, reason: str) -> None:
        if self.active_session_id:
            LOGGER.info("session_close id=%s reason=%s", self.active_session_id, reason)
        self.active_session_id = None

    def _begin_primary_action(self, source: str) -> bool:
        now = time.monotonic()
        if now - self._last_action_at < (self._action_debounce_ms / 1000.0):
            LOGGER.info("primary_action_debounced source=%s state=%s", source, self.state)
            return False
        if self._action_in_flight:
            LOGGER.info("primary_action_blocked source=%s state=%s", source, self.state)
            return False
        self._action_in_flight = True
        self._last_action_at = now
        QTimer.singleShot(self._action_debounce_ms, self._end_primary_action)
        return True

    def _end_primary_action(self) -> None:
        self._action_in_flight = False

    def _new_request_id(self) -> str:
        return uuid.uuid4().hex

    def mode_label(self) -> str:
        if self._groq_api_key:
            return "Groq Cloud"
        return "Qualitaet"

    def set_mode(self, mode: str) -> None:
        if mode in ("hq", "fast"):
            self.mode = mode
        else:
            self.mode = "hq"
        self.settings.setValue("mode.default", self.mode)

    def enter_boot_state(self) -> None:
        self._set_state(STATE_BOOT, reason="boot")
        self._stop_animations()
        self._show_compact()
        self.action_button.setEnabled(True)
        self.action_button.setIcon(self._build_mic_icon("mic"))
        self.action_button.setIconSize(QSize(28, 28))
        self._set_action_style("#2f343a", "#3b424b", "#5c636a", 2)

    def enter_download_state(self, status_text: str = "Lade Fast-Modell herunter ...") -> None:
        self._set_state(STATE_DOWNLOADING, reason="download")
        self._stop_animations()
        self._show_download_layout()
        self.action_button.setEnabled(False)
        self.action_button.setIcon(self._build_mic_icon("mic"))
        self.action_button.setIconSize(QSize(25, 25))
        self._set_action_style("#2f343a", "#3b424b", "#5c636a", 2)
        self.status_label.setText(status_text)
        self.progress_bar.setValue(0)

    def enter_idle_state(self) -> None:
        self._set_state(STATE_IDLE, reason="ready")
        self._stop_animations()
        self._show_compact()
        self.action_button.setEnabled(True)
        self.action_button.setIcon(QIcon())
        self.action_button.setText("")
        self._set_action_style(ACCENT_ORANGE, ACCENT_ORANGE_HOVER, ACCENT_ORANGE_SOFT, 4)

    def enter_starting_state(self) -> None:
        self._set_state(STATE_STARTING, reason="record_arm")
        self._stop_animations()
        self._show_compact()
        self.action_button.setEnabled(False)
        self.action_button.setIcon(QIcon())
        self.action_button.setText(self.spinner_frames[0])
        self._set_action_style("#545d66", "#545d66", ACCENT_ORANGE, 2)
        self.spinner_index = 0
        self.spinner_timer.start()

    def enter_recording_state(self) -> None:
        self._set_state(STATE_RECORDING, reason="record_start")
        self._stop_animations()
        self._show_compact()
        self.action_button.setEnabled(True)
        self.action_button.setIcon(self._build_mic_icon("stop"))
        self.action_button.setIconSize(QSize(26, 26))
        self._set_action_style(ACCENT_ORANGE_DARK, ACCENT_ORANGE, ACCENT_ORANGE_SOFT, 4)
        self.pulse_toggle = False
        self.pulse_timer.start()

    def enter_stopping_state(self) -> None:
        self._set_state(STATE_STOPPING, reason="record_stop")
        self._stop_animations()
        self._show_compact()
        self.action_button.setEnabled(False)
        self.action_button.setIcon(QIcon())
        self.action_button.setText("...")
        self._set_action_style("#545d66", "#545d66", ACCENT_ORANGE, 2)
        self.spinner_index = 0
        self.spinner_timer.start()

    def enter_processing_state(self) -> None:
        self._set_state(STATE_PROCESSING, reason="processing")
        self._stop_animations()
        self._show_compact()
        self.action_button.setEnabled(False)
        self.action_button.setIcon(QIcon())
        self.action_button.setText(self.spinner_frames[0])
        self._set_action_style("#495057", "#495057", ACCENT_ORANGE, 2)
        self.spinner_index = 0
        self.spinner_timer.start()

    def enter_check_state(self) -> None:
        self._set_state(STATE_CHECK, reason="check_flash")
        self._stop_animations()
        self._show_compact()
        self.action_button.setEnabled(False)
        self.action_button.setIcon(self._build_mic_icon("check"))
        self.action_button.setIconSize(QSize(26, 26))
        self._set_action_style("#2f9e44", "#2f9e44", "#d3f9d8", 3)
        self._check_to_copy_timer.start(CHECK_FLASH_MS)
        self._check_failsafe_timer.start(max(1200, CHECK_FLASH_MS * 3))

    def _transition_check_to_copy(self) -> None:
        if self.state == STATE_CHECK:
            LOGGER.info("check_to_copy_timer_fire session=%s", self.active_session_id or "-")
            self.enter_copy_state()

    def _force_copy_if_still_check(self) -> None:
        if self.state != STATE_CHECK:
            return
        LOGGER.warning("check_to_copy_failsafe_fire session=%s", self.active_session_id or "-")
        self.enter_copy_state()

    def enter_copy_state(self) -> None:
        self._set_state(STATE_COPY_READY, reason="copy_ready")
        self._stop_animations()
        self._show_copy_layout()
        self.action_button.setEnabled(True)
        self.action_button.setIcon(self._build_mic_icon("copy"))
        self.action_button.setIconSize(QSize(26, 26))
        self._set_action_style(ACCENT_ORANGE, ACCENT_ORANGE_HOVER, ACCENT_ORANGE_SOFT, 3)

    def enter_error_state(self, message: str, *, code: str = "ERROR") -> None:
        self._set_last_error(code, message)
        self._set_state(STATE_ERROR, reason=code)
        self.enter_idle_state()
        self.notify(APP_NAME, message, QSystemTrayIcon.MessageIcon.Critical)

    def _on_action_clicked(self) -> None:
        self.dispatch_primary_action(source="button")

    def dispatch_primary_action(self, *, source: str) -> None:
        if not self._begin_primary_action(source):
            return
        LOGGER.info("primary_action source=%s state=%s session=%s", source, self.state, self.active_session_id or "-")
        if self.state == STATE_IDLE:
            self.start_recording()
            return
        if self.state == STATE_RECORDING:
            self.stop_and_transcribe()
            return
        if self.state == STATE_COPY_READY:
            self.copy_last_transcript()
            return
        self.notify(APP_NAME, "Aktion aktuell nicht verfuegbar. Falls noetig: Session zuruecksetzen.")

    def trigger_primary_action(self, *, source: str = "tray") -> None:
        self.dispatch_primary_action(source=source)

    def _pulse_tick(self) -> None:
        if self.state != STATE_RECORDING:
            self.pulse_timer.stop()
            return

        self.pulse_toggle = not self.pulse_toggle
        if self.pulse_toggle:
            self._set_action_style("#cc3f12", ACCENT_ORANGE_DARK, ACCENT_ORANGE_SOFT, 4)
        else:
            self._set_action_style(ACCENT_ORANGE_DARK, ACCENT_ORANGE, ACCENT_ORANGE_SOFT, 4)

    def _spinner_tick(self) -> None:
        if self.state not in {STATE_STARTING, STATE_PROCESSING, STATE_STOPPING}:
            self.spinner_timer.stop()
            return

        self.spinner_index = (self.spinner_index + 1) % len(self.spinner_frames)
        self.action_button.setText(self.spinner_frames[self.spinner_index])

    def _on_audio_guard_tick(self) -> None:
        if self.state == STATE_RECORDING:
            return
        if self.state == STATE_CHECK:
            return
        if self.state == STATE_STARTING:
            return
        if self.recorder.is_active():
            LOGGER.error("ghost_recording_detected state=%s", self.state)
            try:
                _ = self.recorder.stop(require_full_chunks=False)
            except Exception:
                pass
        if self.recorder.has_pending_shutdown():
            self.recorder.force_release()

    def ensure_model_available(self) -> None:
        try:
            existing = find_existing_hq_model_path()
            if existing:
                self.hq_model_path = str(existing)
                self.fast_model_path = str(existing)

                if self.mode == "fast" and find_whisper_server():
                    self._start_fast_backend()
                    return

                if self.mode == "fast":
                    LOGGER.warning("whisper-server not found, falling back to hq mode")
                    self.mode = "hq"
                    self.settings.setValue("mode.default", self.mode)

                self.enter_idle_state()
                return

            self._download_hq_model(start_after_ready=False)
        except Exception as exc:
            self.mode = "hq"
            self.enter_idle_state()
            self.notify(APP_NAME, f"Model-Initialisierung fehlgeschlagen: {exc}", QSystemTrayIcon.MessageIcon.Warning)

    def _download_fast_model(self) -> None:
        if self.download_thread and self.download_thread.isRunning():
            return
        request_id = self._new_request_id()
        self._download_request_id = request_id
        self.enter_download_state("Lade Fast-Modell (turbo) herunter ...")
        self.download_thread = ModelDownloadThread(request_id, "Fast-Modell", default_fast_model_path(), fast_model_url())
        self.download_thread.progress.connect(self._on_download_progress)
        self.download_thread.finished_ok.connect(self._on_fast_model_ready)
        self.download_thread.failed.connect(self._on_model_failed)
        self.download_thread.start()

    def _download_hq_model(self, *, start_after_ready: bool) -> None:
        if self.download_thread and self.download_thread.isRunning():
            self.notify(APP_NAME, "Ein anderer Modelldownload laeuft bereits.")
            return
        request_id = self._new_request_id()
        self._download_request_id = request_id
        self._start_recording_after_hq_download = start_after_ready
        self.enter_download_state("Lade Sprachmodell (large-v3) herunter ...")
        self.download_thread = ModelDownloadThread(
            request_id,
            "HQ-Modell",
            default_hq_model_path(),
            hq_model_url(),
            minimum_bytes=900_000_000,
        )
        self.download_thread.progress.connect(self._on_download_progress)
        self.download_thread.finished_ok.connect(self._on_hq_model_ready)
        self.download_thread.failed.connect(self._on_model_failed)
        self.download_thread.start()

    def _on_download_progress(self, request_id: str, percent: int, detail: str) -> None:
        if request_id != self._download_request_id:
            return
        if self.progress_bar.maximum() == 0:
            self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(percent)
        self.status_label.setText(f"{percent}%  {detail}")

    def _on_fast_model_ready(self, request_id: str, path: str) -> None:
        if request_id != self._download_request_id:
            return
        self._download_request_id = None
        self.fast_model_path = path
        self._start_fast_backend()

    def _on_hq_model_ready(self, request_id: str, path: str) -> None:
        if request_id != self._download_request_id:
            return
        self._download_request_id = None
        self.hq_model_path = path
        should_start = self._start_recording_after_hq_download
        self._start_recording_after_hq_download = False
        self.enter_idle_state()
        self.notify(APP_NAME, "HQ-Modell ist bereit.")
        if should_start:
            self.start_recording()

    def _start_fast_backend(self) -> None:
        if not self.fast_model_path:
            self.enter_error_state("Fast-Modell nicht gefunden.", code="FAST_MODEL_MISSING")
            return
        if self.server_warmup_thread and self.server_warmup_thread.isRunning():
            return

        self.enter_download_state("Starte lokale Sprachengine ...")
        self.progress_bar.setRange(0, 0)
        self.status_label.setText("Sprachengine wird vorgewaermt ...")

        if self.server_manager:
            self.server_manager.stop()
            self.server_manager = None
        self.server_manager = WhisperServerProcessManager(self.fast_model_path)
        request_id = self._new_request_id()
        self._warmup_request_id = request_id
        self.server_warmup_thread = ServerWarmupThread(self.server_manager, request_id)
        self.server_warmup_thread.finished_ok.connect(self._on_server_ready)
        self.server_warmup_thread.failed.connect(self._on_server_failed)
        self.server_warmup_thread.start()

    def _on_server_ready(self, request_id: str) -> None:
        if request_id != self._warmup_request_id:
            return
        self._warmup_request_id = None
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.enter_idle_state()
        self.notify(APP_NAME, "Sprachengine bereit (large-v3).")
        if self.server_manager:
            LOGGER.info("server_ready pid=%s port=%s", getattr(self.server_manager, "_owned_pid", None), self.server_manager.port)

        existing_hq = find_existing_hq_model_path()
        if existing_hq:
            self.hq_model_path = str(existing_hq)

    def _on_server_failed(self, request_id: str, error_message: str) -> None:
        if request_id != self._warmup_request_id:
            return
        self._warmup_request_id = None
        self.enter_error_state(f"whisper-server Start fehlgeschlagen: {error_message}", code="SERVER_START_FAILED")

    def _on_model_failed(self, request_id: str, error_message: str) -> None:
        if request_id != self._download_request_id:
            return
        self._download_request_id = None
        self._start_recording_after_hq_download = False
        self.enter_error_state(f"Modelldownload fehlgeschlagen: {error_message}", code="MODEL_DOWNLOAD_FAILED")

    def _drain_record_start_results(self) -> None:
        while True:
            try:
                stale_request_id, stale_error = self._record_start_result_queue.get_nowait()
            except Empty:
                break
            LOGGER.info(
                "discard_record_start_result request=%s error=%s",
                stale_request_id,
                stale_error or "-",
            )
            if stale_error is None and self.recorder.is_active():
                try:
                    _ = self.recorder.stop(require_full_chunks=False)
                except Exception:
                    pass

    def _start_recording_worker(self, request_id: str, *, capture_full_chunks: bool) -> None:
        error_message: str | None = None
        try:
            self.recorder.start(capture_full_chunks=capture_full_chunks)
        except Exception as exc:
            error_message = str(exc)
        self._record_start_result_queue.put((request_id, error_message))

    def _begin_record_start(self, session_id: str, *, capture_full_chunks: bool) -> None:
        if self._record_start_request_id is not None:
            raise RuntimeError("Mikrofon-Start laeuft bereits.")
        self._drain_record_start_results()
        request_id = self._new_request_id()
        self._record_start_request_id = request_id
        self._record_start_session_id = session_id
        self._record_start_deadline = time.monotonic() + self._record_start_timeout_seconds
        self.enter_starting_state()
        self._record_start_thread = threading.Thread(
            target=self._start_recording_worker,
            args=(request_id,),
            kwargs={"capture_full_chunks": capture_full_chunks},
            daemon=True,
        )
        self._record_start_thread.start()
        self._record_start_timer.start()
        LOGGER.info("record_start_requested session=%s request=%s mode=%s", session_id, request_id, self.mode)

    def _cancel_pending_record_start(self, *, reason: str) -> None:
        if self._record_start_request_id is None:
            return
        LOGGER.warning(
            "record_start_cancel request=%s session=%s reason=%s",
            self._record_start_request_id,
            self._record_start_session_id or "-",
            reason,
        )
        self._record_start_request_id = None
        self._record_start_session_id = None
        self._record_start_thread = None
        self._record_start_deadline = 0.0
        self._record_start_timer.stop()

    def _retry_record_start(self, session_id: str) -> None:
        if not self._is_session_active(session_id):
            LOGGER.info("skip_record_start_retry session=%s active=%s", session_id, self.active_session_id)
            return
        try:
            self._begin_record_start(session_id, capture_full_chunks=(self.mode == "hq"))
        except Exception as exc:
            if self.stream_controller:
                self.stream_controller.cancel()
                self.stream_controller = None
            self._record_start_retry_count = 0
            self._close_session(reason="record_start_retry_failed")
            self.enter_error_state(f"Mikrofon nicht verfuegbar: {exc}", code="MIC_UNAVAILABLE")

    def _poll_record_start(self) -> None:
        active_request = self._record_start_request_id
        if active_request is None:
            self._record_start_timer.stop()
            return

        while True:
            try:
                request_id, error_message = self._record_start_result_queue.get_nowait()
            except Empty:
                break

            if request_id != active_request:
                LOGGER.info(
                    "stale_record_start_result request=%s active=%s error=%s",
                    request_id,
                    active_request,
                    error_message or "-",
                )
                if error_message is None and self.recorder.is_active():
                    try:
                        _ = self.recorder.stop(require_full_chunks=False)
                    except Exception:
                        pass
                continue

            self._record_start_timer.stop()
            self._record_start_request_id = None
            session_id = self._record_start_session_id
            self._record_start_session_id = None
            self._record_start_thread = None
            self._record_start_deadline = 0.0

            if not self._is_session_active(session_id):
                LOGGER.info("stale_record_start_callback session=%s active=%s", session_id, self.active_session_id)
                if error_message is None and self.recorder.is_active():
                    try:
                        _ = self.recorder.stop(require_full_chunks=False)
                    except Exception:
                        pass
                return

            if error_message is not None:
                if self.stream_controller:
                    self.stream_controller.cancel()
                    self.stream_controller = None
                self._record_start_retry_count = 0
                self._close_session(reason="record_start_failed")
                self.enter_error_state(f"Mikrofon nicht verfuegbar: {error_message}", code="MIC_UNAVAILABLE")
                return

            self._record_start_retry_count = 0
            self.stream_started_at = time.monotonic()
            if self.mode == "fast":
                self.stream_capture_timer.start()
            self.enter_recording_state()
            LOGGER.info("recording_started session=%s mode=%s", session_id, self.mode)
            self.notify(APP_NAME, f"Aufnahme gestartet ({self.mode_label()}).")
            return

        if time.monotonic() < self._record_start_deadline:
            return

        timeout_s = self._record_start_timeout_seconds
        timed_out_request = self._record_start_request_id
        LOGGER.error("record_start_timeout request=%s timeout=%.2fs", timed_out_request, timeout_s)
        session_id = self._record_start_session_id
        self._cancel_pending_record_start(reason="start_timeout")
        self.recorder.force_release()

        if self._is_session_active(session_id) and self._record_start_retry_count < MIC_START_RETRY_MAX:
            self._record_start_retry_count += 1
            retry_index = self._record_start_retry_count
            LOGGER.warning(
                "record_start_retry session=%s attempt=%s/%s",
                session_id,
                retry_index,
                MIC_START_RETRY_MAX,
            )
            self.notify(APP_NAME, f"Mikrofon reagiert nicht, neuer Versuch ({retry_index}/{MIC_START_RETRY_MAX}) ...")
            QTimer.singleShot(MIC_START_RETRY_DELAY_MS, lambda sid=session_id: self._retry_record_start(sid))
            return

        if self.stream_controller:
            self.stream_controller.cancel()
            self.stream_controller = None
        self._record_start_retry_count = 0
        self._close_session(reason="record_start_timeout")
        self.enter_error_state(
            f"Mikrofon-Start blockiert nach {timeout_s:.1f}s. Bitte Session zuruecksetzen oder App neu starten.",
            code="MIC_START_TIMEOUT",
        )

    def start_recording(self) -> None:
        if self.state not in {STATE_IDLE, STATE_COPY_READY}:
            self.notify(APP_NAME, "Engine ist beschaeftigt. Falls noetig: Session zuruecksetzen.")
            return
        if self.active_session_id is not None:
            self.enter_error_state("Session ist noch aktiv. Bitte Session zuruecksetzen.", code="SESSION_ALREADY_ACTIVE")
            return
        if self.recorder.is_active():
            self.enter_error_state("Recording laeuft bereits. Bitte Session zuruecksetzen.", code="RECORDER_ALREADY_ACTIVE")
            return

        if self.mode == "hq":
            if not self.hq_model_path:
                existing_hq = find_existing_hq_model_path()
                if existing_hq:
                    self.hq_model_path = str(existing_hq)
                else:
                    self._download_hq_model(start_after_ready=True)
                    return
        else:
            if not self.server_manager:
                self.enter_error_state("Fast-Engine ist nicht bereit.", code="FAST_ENGINE_NOT_READY")
                return
            if not self.server_manager.is_healthy():
                self._start_fast_backend()
                return

        try:
            session_id = self._start_session()
            self._record_start_retry_count = 0
            self.stream_controller = None
            if self.mode == "fast":
                self.stream_controller = StreamingTranscriptionController(
                    self.server_manager.inference_url,
                    sample_rate=SAMPLE_RATE,
                    chunk_ms=self.stream_chunk_ms,
                    overlap_ms=self.stream_overlap_ms,
                    max_queue_chunks=self._max_queue_chunks,
                )
            self._begin_record_start(session_id, capture_full_chunks=(self.mode == "hq"))
        except Exception as exc:
            if self.stream_controller:
                self.stream_controller.cancel()
                self.stream_controller = None
            self._close_session(reason="record_start_failed")
            self.enter_error_state(f"Mikrofon nicht verfuegbar: {exc}", code="MIC_UNAVAILABLE")

    def stop_and_transcribe(self) -> None:
        if self.state != STATE_RECORDING:
            self.notify(APP_NAME, "Keine aktive Aufnahme.")
            return
        if self.mode == "hq":
            self.stop_and_transcribe_hq()
            return
        self.stop_and_finalize_stream()

    def _on_stream_capture_tick(self) -> None:
        if self.state != STATE_RECORDING or self.mode != "fast" or not self.stream_controller:
            return

        try:
            pending = self.recorder.consume_pending_samples()
        except Exception:
            return
        if pending.size > 0:
            self.stream_controller.add_audio_samples(pending)

    def stop_and_finalize_stream(self) -> None:
        session_id = self.active_session_id
        if not self._is_session_active(session_id):
            self.enter_error_state("Streaming-Session nicht aktiv.", code="SESSION_INACTIVE")
            return
        if not self.stream_controller:
            self.enter_error_state("Streaming-Session nicht aktiv.", code="STREAM_NOT_ACTIVE")
            return

        try:
            self._on_stream_capture_tick()
            _ = self.recorder.stop(require_full_chunks=False)
        except Exception as exc:
            if self.stream_controller:
                self.stream_controller.cancel()
                self.stream_controller = None
            self._close_session(reason="stream_stop_failed")
            self.enter_error_state(str(exc), code="STOP_FAILED")
            return

        self.stream_capture_timer.stop()
        pending_tail = self.recorder.consume_pending_samples()
        if pending_tail.size > 0:
            self.stream_controller.add_audio_samples(pending_tail)
        self.recorder.force_release()

        self.enter_stopping_state()
        stop_clicked_at = time.monotonic()
        queue_depth = self.stream_controller.queue_depth()
        LOGGER.info("stop_clicked session=%s queue_depth=%s", session_id, queue_depth)
        self.stream_finalize_thread = StreamFinalizeThread(
            session_id,
            self.stream_controller,
            stop_clicked_at,
            self._stopping_timeout_seconds,
        )
        self.stream_finalize_thread.finished_ok.connect(self._on_stream_transcript_ready)
        self.stream_finalize_thread.failed.connect(self._on_transcript_failed)
        self.stream_finalize_thread.start()

    def _on_stream_transcript_ready(self, session_id: str, text: str, fully_processed: bool, stop_latency_ms: float) -> None:
        if not self._is_session_active(session_id):
            LOGGER.info("stale_stream_callback session=%s active=%s", session_id, self.active_session_id)
            return
        self.stream_controller = None
        self.enter_processing_state()
        self.last_transcript = text
        self.enter_check_state()

        latency_hint = f"{stop_latency_ms:.0f}ms"
        if fully_processed:
            self.notify(APP_NAME, f"Transkription abgeschlossen ({latency_hint}).")
        else:
            self.notify(
                APP_NAME,
                f"Teiltranskript bereit ({latency_hint}, Timeout beim Flush). Bei Bedarf: Session zuruecksetzen.",
                QSystemTrayIcon.MessageIcon.Warning,
            )
        LOGGER.info(
            "stop_latency_ms=%.0f mode=%s complete=%s session=%s",
            stop_latency_ms,
            self.mode,
            fully_processed,
            session_id,
        )

    def stop_and_transcribe_hq(self) -> None:
        session_id = self.active_session_id
        if not self._is_session_active(session_id):
            self.enter_error_state("Session nicht aktiv.", code="SESSION_INACTIVE")
            return
        if not self.hq_model_path:
            self.enter_error_state("HQ-Modell nicht gefunden.", code="HQ_MODEL_MISSING")
            return

        try:
            chunks = self.recorder.stop()
        except Exception as exc:
            self._close_session(reason="hq_stop_failed")
            self.enter_error_state(str(exc), code="STOP_FAILED")
            return

        recorded_seconds = max(0.0, time.monotonic() - self.stream_started_at)
        if recorded_seconds >= 45.0 and not (self._remote_server_url and self._remote_server_healthy):
            self.notify(
                APP_NAME,
                f"Large-v3 bei {int(recorded_seconds)}s Aufnahme kann laenger dauern.",
                QSystemTrayIcon.MessageIcon.Warning,
            )

        self.stream_capture_timer.stop()
        _ = self.recorder.consume_pending_samples()
        self.recorder.force_release()
        self.enter_stopping_state()
        self.finalize_thread = FinalizeRecordingThread(session_id, chunks, SAMPLE_RATE)
        self.finalize_thread.finished_ok.connect(self._on_wav_ready)
        self.finalize_thread.failed.connect(self._on_transcript_failed)
        self.finalize_thread.start()

    def _on_wav_ready(self, session_id: str, wav_path: str) -> None:
        if not self._is_session_active(session_id):
            Path(wav_path).unlink(missing_ok=True)
            LOGGER.info("stale_hq_wav_callback session=%s active=%s", session_id, self.active_session_id)
            return
        self.current_wav_path = Path(wav_path)

        # Priority 1: Groq API (fastest, same quality)
        if self._groq_api_key:
            self.enter_processing_state()
            LOGGER.info("transcribe_groq session=%s", session_id)
            self.transcribe_thread = GroqTranscribeThread(
                session_id, self.current_wav_path, self._groq_api_key
            )
            self.transcribe_thread.finished_ok.connect(self._on_transcript_ready)
            self.transcribe_thread.failed.connect(self._on_groq_failed_fallback)
            self.transcribe_thread.start()
            return

        # Priority 2: Custom remote server
        if self._remote_server_url and self._remote_server_healthy:
            self.enter_processing_state()
            LOGGER.info("transcribe_remote url=%s session=%s", self._remote_server_url, session_id)
            self.transcribe_thread = RemoteTranscribeThread(
                session_id, self.current_wav_path, self._remote_server_url, self._remote_api_key
            )
            self.transcribe_thread.finished_ok.connect(self._on_transcript_ready)
            self.transcribe_thread.failed.connect(self._on_remote_failed_fallback_local)
            self.transcribe_thread.start()
            return

        # Priority 3: Local whisper-cli
        if not self.hq_model_path:
            self._close_session(reason="hq_missing_model")
            self.enter_error_state("HQ-Modell nicht gefunden.", code="HQ_MODEL_MISSING")
            return

        self.enter_processing_state()
        self.transcribe_thread = TranscribeThread(session_id, self.hq_model_path, self.current_wav_path)
        self.transcribe_thread.finished_ok.connect(self._on_transcript_ready)
        self.transcribe_thread.failed.connect(self._on_transcript_failed)
        self.transcribe_thread.start()

    def _on_groq_failed_fallback(self, session_id: str, error_message: str) -> None:
        """Groq API failed -- fall back to local whisper-cli."""
        LOGGER.warning("groq_transcribe_failed error=%s fallback=local session=%s", error_message, session_id)
        if not self._is_session_active(session_id):
            return
        if not self.hq_model_path or not self.current_wav_path:
            self._on_transcript_failed(session_id, f"Groq-Fehler und kein lokales Modell: {error_message}")
            return

        self.notify(APP_NAME, "Groq nicht erreichbar, nutze lokales Modell ...", QSystemTrayIcon.MessageIcon.Warning)
        self.transcribe_thread = TranscribeThread(session_id, self.hq_model_path, self.current_wav_path)
        self.transcribe_thread.finished_ok.connect(self._on_transcript_ready)
        self.transcribe_thread.failed.connect(self._on_transcript_failed)
        self.transcribe_thread.start()

    def _on_remote_failed_fallback_local(self, session_id: str, error_message: str) -> None:
        """Remote transcription failed — fall back to local whisper-cli."""
        LOGGER.warning("remote_transcribe_failed error=%s fallback=local session=%s", error_message, session_id)
        self._remote_server_healthy = False

        if not self._is_session_active(session_id):
            return
        if not self.hq_model_path or not self.current_wav_path:
            self._on_transcript_failed(session_id, f"Server-Fehler und kein lokales Modell: {error_message}")
            return

        self.notify(APP_NAME, "Server nicht erreichbar, nutze lokales Modell ...", QSystemTrayIcon.MessageIcon.Warning)
        self.transcribe_thread = TranscribeThread(session_id, self.hq_model_path, self.current_wav_path)
        self.transcribe_thread.finished_ok.connect(self._on_transcript_ready)
        self.transcribe_thread.failed.connect(self._on_transcript_failed)
        self.transcribe_thread.start()

    def _on_transcript_ready(self, session_id: str, text: str) -> None:
        if not self._is_session_active(session_id):
            LOGGER.info("stale_transcript_callback session=%s active=%s", session_id, self.active_session_id)
            return
        self.last_transcript = text
        if self.current_wav_path:
            self.current_wav_path.unlink(missing_ok=True)
            self.current_wav_path = None

        self.enter_check_state()
        self.notify(APP_NAME, "Transkription abgeschlossen.")

    def _on_transcript_failed(self, session_id: str, error_message: str) -> None:
        if session_id and not self._is_session_active(session_id):
            LOGGER.info("stale_error_callback session=%s active=%s error=%s", session_id, self.active_session_id, error_message)
            return
        if self.stream_controller:
            self.stream_controller.cancel()
            self.stream_controller = None
        self.stream_capture_timer.stop()

        if self.current_wav_path:
            self.current_wav_path.unlink(missing_ok=True)
            self.current_wav_path = None

        self._close_session(reason="transcript_failed")
        if error_message == "PROCESSING_TIMEOUT":
            self.enter_error_state(
                "Stop-Flush Timeout. Nutze 'Session zuruecksetzen' und versuche es erneut.",
                code="PROCESSING_TIMEOUT",
            )
            return
        self.enter_error_state(f"Transkription fehlgeschlagen: {error_message}", code="TRANSCRIBE_FAILED")

    def copy_last_transcript(self) -> None:
        if not self.last_transcript.strip():
            self.enter_error_state("Nichts zum Kopieren vorhanden.", code="COPY_EMPTY")
            return

        try:
            copy_text_to_clipboard(self.last_transcript)
            session_id = self.active_session_id
            self.last_transcript = ""
            self._close_session(reason="copied")
            self.recorder.force_release()
            self.enter_idle_state()
            LOGGER.info("copied_to_clipboard session=%s", session_id or "-")
            self.notify(APP_NAME, "Text wurde ins Clipboard kopiert.")
        except Exception as exc:
            self.enter_error_state(f"Clipboard Fehler: {exc}", code="CLIPBOARD_ERROR")

    def download_hq_model_now(self) -> None:
        if self.server_warmup_thread and self.server_warmup_thread.isRunning():
            self.notify(APP_NAME, "Bitte warten, Engine startet noch.")
            return
        existing = find_existing_hq_model_path()
        if existing:
            self.hq_model_path = str(existing)
            self.notify(APP_NAME, "HQ-Modell ist bereits vorhanden.")
            return
        if self.state == STATE_RECORDING:
            self.notify(APP_NAME, "Bitte Aufnahme zuerst stoppen.", QSystemTrayIcon.MessageIcon.Warning)
            return
        self._download_hq_model(start_after_ready=False)

    def reset_session(self, *, notify_user: bool = True) -> None:
        LOGGER.info("session_reset requested state=%s session=%s", self.state, self.active_session_id or "-")
        self._cancel_pending_record_start(reason="manual_reset")
        self._record_start_retry_count = 0
        self.stream_capture_timer.stop()
        self._record_start_timer.stop()
        self.spinner_timer.stop()
        self.pulse_timer.stop()
        if self.stream_controller:
            self.stream_controller.cancel()
            self.stream_controller = None
        if self.recorder.is_active():
            try:
                _ = self.recorder.stop(require_full_chunks=False)
            except Exception:
                pass
        try:
            _ = self.recorder.consume_pending_samples()
        except Exception:
            pass
        if self.current_wav_path:
            self.current_wav_path.unlink(missing_ok=True)
            self.current_wav_path = None
        self.last_transcript = ""
        self.recorder.force_release()
        self._drain_record_start_results()
        self._close_session(reason="manual_reset")
        self.enter_idle_state()
        if notify_user:
            self.notify(APP_NAME, "Session wurde zurueckgesetzt.")

    def restart_engine(self) -> None:
        LOGGER.info("engine_restart requested")
        self.reset_session(notify_user=False)
        self.ensure_model_available()
        self.notify(APP_NAME, "Engine wird neu initialisiert ...")

    def shutdown(self) -> None:
        self.reset_session(notify_user=False)
        if self.server_manager:
            self.server_manager.stop()
            self.server_manager = None

    def notify(self, title: str, message: str, icon=QSystemTrayIcon.MessageIcon.Information) -> None:
        if self.notify_callback:
            self.notify_callback(title, message, icon)

    def restore_position(self) -> None:
        saved = self.settings.value("window_position")
        if isinstance(saved, QPoint):
            self.move(saved)
        else:
            self.move(self._default_window_position())

        self.ensure_visible_on_any_screen()

    def _default_window_position(self) -> QPoint:
        screen = QApplication.primaryScreen()
        if not screen:
            return QPoint(1000, 100)

        rect = screen.availableGeometry()
        return QPoint(rect.right() - (WIDGET_COMPACT_WIDTH + 50), rect.top() + 90)

    def ensure_visible_on_any_screen(self) -> None:
        widget_rect = self.frameGeometry()
        for screen in QGuiApplication.screens():
            if screen.availableGeometry().intersects(widget_rect):
                return

        self.move(self._default_window_position())
        self.persist_position()

    def reset_position_to_default(self) -> None:
        self.move(self._default_window_position())
        self.persist_position()

    def persist_position(self) -> None:
        self.settings.setValue("window_position", self.pos())


class VoiceClipApp:
    def __init__(self) -> None:
        self.qt_app = QApplication(sys.argv)
        self.qt_app.setApplicationName(APP_NAME)
        self.qt_app.setOrganizationName(ORGANIZATION_NAME)
        self.qt_app.setOrganizationDomain(ORGANIZATION_DOMAIN)
        self.qt_app.setQuitOnLastWindowClosed(False)

        self.instance_guard = SingleInstanceGuard(INSTANCE_SERVER_NAME)

        if not self.instance_guard.acquire_or_raise_existing():
            request_foreground_activation()
            raise SystemExit(0)

        write_pid_file(os.getpid())
        LOGGER.info("app_start cleanup_mode=%s", server_cleanup_mode_setting())
        WhisperServerProcessManager.cleanup_registered_servers()
        self.window = VoiceClipWidget()
        self.window.notify_callback = self.show_notification
        self.window.state_callback = self.on_window_state_changed

        self.instance_guard.on_raise_request = self.raise_existing_window

        self._tray_state = self.window.state
        self._tray_phase = 0
        self.tray_anim_timer = QTimer(self.qt_app)
        self.tray_anim_timer.setInterval(190)
        self.tray_anim_timer.timeout.connect(self._on_tray_anim_tick)
        self._tray_action_debounce_ms = action_debounce_ms_setting()
        self._last_tray_action_at = 0.0

        self.tray_icon = QSystemTrayIcon(self._build_tray_icon(self._tray_state, self._tray_phase), self.qt_app)
        self.tray_menu = QMenu()

        self.primary_action = QAction("Aufnahme starten", self.qt_app)
        self.primary_action.triggered.connect(self.handle_primary_tray_action)
        self.tray_menu.addAction(self.primary_action)

        if self.window._remote_server_url and self.window._remote_server_healthy:
            mode_label = "Server-Modus (remote)"
        elif self.window._remote_server_url:
            mode_label = "Lokal (Server nicht erreichbar)"
        else:
            mode_label = "Qualitaetsmodus: large-v3"
        self.model_info_action = QAction(mode_label, self.qt_app)
        self.model_info_action.setEnabled(False)
        self.tray_menu.addAction(self.model_info_action)

        self.download_hq_action = QAction("Modell jetzt laden", self.qt_app)
        self.download_hq_action.triggered.connect(self.window.download_hq_model_now)
        self.tray_menu.addAction(self.download_hq_action)

        self.reset_session_action = QAction("Session zuruecksetzen", self.qt_app)
        self.reset_session_action.triggered.connect(lambda: self.window.reset_session())
        self.tray_menu.addAction(self.reset_session_action)

        self.restart_engine_action = QAction("Engine neu starten", self.qt_app)
        self.restart_engine_action.triggered.connect(lambda: self.window.restart_engine())
        self.tray_menu.addAction(self.restart_engine_action)

        self.tray_menu.addSeparator()

        self.toggle_window_action = QAction("Floating Widget zeigen/ausblenden", self.qt_app)
        self.toggle_window_action.triggered.connect(self.toggle_window)
        self.tray_menu.addAction(self.toggle_window_action)

        self.reset_window_action = QAction("Fensterposition zuruecksetzen", self.qt_app)
        self.reset_window_action.triggered.connect(self.reset_window_position)
        self.tray_menu.addAction(self.reset_window_action)

        self.autostart_action = QAction("Beim Login starten", self.qt_app)
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(login_item_enabled())
        self.autostart_action.triggered.connect(self.toggle_login_item)
        self.tray_menu.addAction(self.autostart_action)

        self.tray_menu.addSeparator()

        self.quit_action = QAction("Quit", self.qt_app)
        self.quit_action.triggered.connect(self.quit)
        self.tray_menu.addAction(self.quit_action)

        self.tray_icon.setToolTip(APP_NAME)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()

        self.window.hide()
        self.on_window_state_changed(self.window.state)

    def _build_tray_icon(self, state: str, phase: int = 0) -> QIcon:
        size = 24
        screen = QGuiApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 2.0
        if dpr < 1.0:
            dpr = 1.0

        pixel_size = int(round(size * dpr))
        pixmap = QPixmap(pixel_size, pixel_size)
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        icon_white = QColor("#f6f8ff")
        icon_white_soft = QColor("#dfe7f0")

        if state == STATE_RECORDING:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(icon_white_soft if (phase % 2) else icon_white)
            painter.drawRoundedRect(7, 7, 10, 10, 2.8, 2.8)
        elif state in {STATE_STARTING, STATE_STOPPING, STATE_PROCESSING, STATE_DOWNLOADING, STATE_BOOT}:
            dot_pattern = [1, 2, 3, 2]
            active_dots = dot_pattern[phase % len(dot_pattern)]
            x_positions = (8, 12, 16)
            y = 12
            radius = 1.85
            painter.setPen(Qt.PenStyle.NoPen)
            for idx, x in enumerate(x_positions):
                color = QColor(icon_white)
                if idx < active_dots:
                    color.setAlpha(245)
                else:
                    color.setAlpha(95)
                painter.setBrush(color)
                painter.drawEllipse(QRectF(x - radius, y - radius, radius * 2.0, radius * 2.0))
        elif state == STATE_CHECK:
            pen = QPen(icon_white, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawLine(5, 13, 10, 18)
            painter.drawLine(10, 18, 19, 7)
        elif state == STATE_COPY_READY:
            pen = QPen(icon_white, 1.45, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(10, 4, 9, 11, 2.3, 2.3)
            painter.drawRoundedRect(5, 8, 10, 12, 2.4, 2.4)
        else:
            svg_pixmap = tinted_svg_icon_pixmap(MIC_ICON_SVG_NAME, size, icon_white, dpr=dpr)
            if svg_pixmap and not svg_pixmap.isNull():
                painter.drawPixmap(0, 0, svg_pixmap)
            else:
                # Fallback if svg asset is unavailable.
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(icon_white)
                painter.drawRoundedRect(9, 4, 6, 10, 3.2, 3.2)

                pen_sub = QPen(icon_white, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen_sub)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawArc(7, 10, 10, 7, 180 * 16, -180 * 16)
                painter.drawLine(12, 16, 12, 19)
                painter.drawLine(9, 19, 15, 19)

        painter.end()
        return QIcon(pixmap)

    def _state_uses_tray_animation(self, state: str) -> bool:
        return state in {STATE_RECORDING, STATE_STARTING, STATE_STOPPING, STATE_PROCESSING, STATE_DOWNLOADING, STATE_BOOT}

    def _tray_tooltip_for_state(self, state: str) -> str:
        mapping = {
            STATE_BOOT: f"{APP_NAME} - Startet",
            STATE_DOWNLOADING: f"{APP_NAME} - Lade Modell",
            STATE_IDLE: f"{APP_NAME} - Klick zum Aufnehmen",
            STATE_STARTING: f"{APP_NAME} - Starte Mikrofon",
            STATE_RECORDING: f"{APP_NAME} - Aufnahme laeuft (klicken = Stop)",
            STATE_STOPPING: f"{APP_NAME} - Stoppe Aufnahme",
            STATE_PROCESSING: f"{APP_NAME} - Transkribiere",
            STATE_CHECK: f"{APP_NAME} - Fertig",
            STATE_COPY_READY: f"{APP_NAME} - Klick zum Kopieren",
        }
        return mapping.get(state, APP_NAME)

    def _primary_action_label_for_state(self, state: str) -> str:
        mapping = {
            STATE_IDLE: "Aufnahme starten",
            STATE_STARTING: "Starte Mikrofon ...",
            STATE_RECORDING: "Aufnahme stoppen",
            STATE_STOPPING: "Stoppt ...",
            STATE_PROCESSING: "Transkribiere ...",
            STATE_CHECK: "Fertig ...",
            STATE_COPY_READY: "Kopieren",
            STATE_DOWNLOADING: "Modell wird geladen ...",
            STATE_BOOT: "Startet ...",
        }
        return mapping.get(state, "Aktion")

    def _update_tray_icon(self) -> None:
        self.tray_icon.setIcon(self._build_tray_icon(self._tray_state, self._tray_phase))
        self.tray_icon.setToolTip(self._tray_tooltip_for_state(self._tray_state))

    def _on_tray_anim_tick(self) -> None:
        self._tray_phase = (self._tray_phase + 1) % 1000
        self._update_tray_icon()

    def on_window_state_changed(self, state: str) -> None:
        self._tray_state = state
        self._tray_phase = 0
        self._update_tray_icon()
        self.primary_action.setText(self._primary_action_label_for_state(state))
        self.primary_action.setEnabled(state in {STATE_IDLE, STATE_RECORDING, STATE_COPY_READY})
        if self._state_uses_tray_animation(state):
            if not self.tray_anim_timer.isActive():
                self.tray_anim_timer.start()
        else:
            self.tray_anim_timer.stop()

    def handle_primary_tray_action(self) -> None:
        now = time.monotonic()
        if now - self._last_tray_action_at < (self._tray_action_debounce_ms / 1000.0):
            return
        self._last_tray_action_at = now

        if self.window.state in {STATE_BOOT, STATE_DOWNLOADING, STATE_STARTING, STATE_STOPPING, STATE_PROCESSING, STATE_CHECK}:
            if self.window.state in {STATE_STOPPING, STATE_PROCESSING}:
                self.show_notification(APP_NAME, "Transkription laeuft bereits.")
            elif self.window.state == STATE_STARTING:
                self.show_notification(APP_NAME, "Mikrofon wird gestartet ...")
            elif self.window.state == STATE_DOWNLOADING:
                self.show_notification(APP_NAME, "Modell wird noch heruntergeladen.")
            elif self.window.state == STATE_BOOT:
                self.show_notification(APP_NAME, "App startet noch.")
            return
        self.window.trigger_primary_action(source="tray")

    def _show_floating_window(self) -> None:
        self.window.ensure_visible_on_any_screen()
        self.window._install_overlay_behavior()
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        request_foreground_activation()

    def raise_existing_window(self) -> None:
        if self.window.isVisible():
            self._show_floating_window()
            return
        self.show_notification(APP_NAME, "voiceClip laeuft bereits in der Menueleiste.")

    def reset_window_position(self) -> None:
        self.window.reset_position_to_default()
        self._show_floating_window()

    def toggle_window(self) -> None:
        if self.window.isVisible():
            self.window.hide()
            return
        self._show_floating_window()

    def on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Context:
            self.tray_menu.popup(QCursor.pos())
            return
        self.handle_primary_tray_action()

    def toggle_login_item(self, enabled: bool) -> None:
        try:
            set_login_item_enabled(enabled)
            self.autostart_action.setChecked(login_item_enabled())
            if enabled:
                self.show_notification(APP_NAME, "Autostart aktiviert.")
            else:
                self.show_notification(APP_NAME, "Autostart deaktiviert.")
        except Exception as exc:
            self.autostart_action.setChecked(login_item_enabled())
            self.show_notification(APP_NAME, f"Autostart konnte nicht gesetzt werden: {exc}", QSystemTrayIcon.MessageIcon.Warning)

    def show_notification(self, title: str, message: str, icon=QSystemTrayIcon.MessageIcon.Information) -> None:
        self.tray_icon.showMessage(title, message, icon, 3000)

    def quit(self) -> None:
        LOGGER.info("app_quit_requested pid=%s", os.getpid())
        try:
            self.window.shutdown()
        except Exception:
            pass
        try:
            self.tray_menu.hide()
        except Exception:
            pass
        self.tray_icon.hide()
        clear_pid_file(os.getpid())
        QTimer.singleShot(900, lambda: os._exit(0))
        self.qt_app.quit()

    def run(self) -> int:
        return self.qt_app.exec()


def main() -> int:
    app = VoiceClipApp()
    return app.run()


if __name__ == "__main__":
    worker_exit_code = run_audio_worker_from_argv(sys.argv[1:])
    if worker_exit_code is not None:
        raise SystemExit(worker_exit_code)
    raise SystemExit(main())
