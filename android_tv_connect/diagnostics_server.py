"""Local Unix-socket diagnostics IPC for Cursor agents and shell tooling."""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from .audio_source_test import (
    AudioAuditionPlayer,
    AudioTestSource,
    build_audio_test_queue,
    next_queue_index,
)
from .branding import VERSION
from .capture_device import (
    build_audio_source_segment,
    capture_device_status,
    invalidate_capture_cache,
    pipewiresrc_available,
    resolve_audio_device,
)
from .media_enumeration import enumerate_audio_sources, enumerate_v4l2_devices
from .singleton import is_ui_running

LOG = logging.getLogger(__name__)

DIAGNOSTICS_DIR = Path.home() / ".local" / "share" / "android-tv-connect"
DIAGNOSTICS_SOCKET = DIAGNOSTICS_DIR / "diagnostics.sock"

_MAIN_THREAD_TIMEOUT_S = 12.0
_MAX_REQUEST_BYTES = 65536
_DEFAULT_LOG_LINES = 100


class DiagnosticsBackend(Protocol):
    """Application hooks used by the diagnostics command handlers."""

    def diagnostics_status(self) -> dict[str, Any]: ...

    def diagnostics_enumerate(self) -> dict[str, Any]: ...

    def diagnostics_audio_play(self, source: str) -> dict[str, Any]: ...

    def diagnostics_audio_stop(self) -> dict[str, Any]: ...

    def diagnostics_audio_test_next(self) -> dict[str, Any]: ...

    def diagnostics_capture_restart(self) -> dict[str, Any]: ...


@dataclass
class RingBufferLogHandler(logging.Handler):
    """Retain the last *capacity* formatted log lines for diagnostics queries."""

    capacity: int = 500
    _lines: deque[str] = field(default_factory=deque, init=False)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            return
        self._lines.append(message)
        while len(self._lines) > self.capacity:
            self._lines.popleft()

    def tail(self, lines: int) -> list[str]:
        count = max(1, min(lines, self.capacity))
        return list(self._lines)[-count:]


_RING_HANDLER: RingBufferLogHandler | None = None


def install_diagnostics_logging() -> RingBufferLogHandler:
    """Attach a ring-buffer handler to the root logger (idempotent)."""
    global _RING_HANDLER
    if _RING_HANDLER is not None:
        return _RING_HANDLER

    handler = RingBufferLogHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.getLogger().addHandler(handler)
    _RING_HANDLER = handler
    return handler


def diagnostics_log_lines(lines: int = _DEFAULT_LOG_LINES) -> list[str]:
    if _RING_HANDLER is None:
        return []
    return _RING_HANDLER.tail(lines)


def diagnostics_socket_path() -> Path:
    return DIAGNOSTICS_SOCKET


def parse_request(raw: str | bytes) -> dict[str, Any]:
    """Parse one newline-delimited JSON diagnostics request."""
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace").strip()
    else:
        text = raw.strip()
    if not text:
        raise ValueError("empty request")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("request must be a JSON object")
    command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("missing command")
    args = payload.get("args", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ValueError("args must be a JSON object")
    return {"command": command.strip(), "args": args}


def format_response(*, ok: bool, data: Any = None, error: str = "") -> str:
    """Serialize a diagnostics response as newline-terminated JSON."""
    payload: dict[str, Any] = {"ok": ok}
    if ok:
        payload["data"] = data
    else:
        payload["error"] = error or "unknown error"
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _pipeline_element_for_device(device: str) -> str:
    segment = build_audio_source_segment(device)
    if "pipewiresrc" in segment:
        return "pipewiresrc"
    if "pulsesrc" in segment:
        return "pulsesrc"
    return "unknown"


def _run_on_gtk_main(func: Callable[[], Any], *, timeout_s: float = _MAIN_THREAD_TIMEOUT_S) -> Any:
    """Run *func* on the GTK main loop and block the caller until it finishes."""
    holder: dict[str, Any] = {}
    done = threading.Event()

    def idle() -> bool:
        try:
            holder["result"] = func()
        except Exception as exc:
            holder["error"] = str(exc)
        finally:
            done.set()
        return False

    GLib.idle_add(idle)
    if not done.wait(timeout=timeout_s):
        raise TimeoutError("timed out waiting for GTK main thread")
    if "error" in holder:
        raise RuntimeError(holder["error"])
    return holder.get("result")


class AppDiagnosticsBackend:
    """GTK-safe diagnostics operations bound to the main window."""

    def __init__(self, window: Any) -> None:
        self._window = window
        self._audition = AudioAuditionPlayer()
        self._audio_queue: list[AudioTestSource] = []
        self._audio_queue_index = 0

    def _capture(self):
        return self._window._capture

    def _config(self):
        return self._window._config

    def _refresh_audio_queue(self) -> list[AudioTestSource]:
        cfg = self._config().capture
        self._audio_queue = build_audio_test_queue(
            enumerate_audio_sources(),
            manual_name="" if cfg.audio_device in ("", "auto") else cfg.audio_device,
            include_auto_resolved=resolve_audio_device(cfg),
            auto_label="Auto (capture dongle)",
        )
        return self._audio_queue

    def _audition_status(self) -> dict[str, Any]:
        return {
            "playing": self._audition.is_playing(),
            "source": self._audition.device or None,
            "app_pid": os.getpid(),
        }

    def diagnostics_status(self) -> dict[str, Any]:
        capture = self._capture()
        cfg = self._config()
        effective_audio = capture.effective_audio_device or resolve_audio_device(cfg.capture)
        effective_video = capture.effective_video_device or cfg.capture.video_device
        usb_status = capture_device_status(cfg.capture)
        element = _pipeline_element_for_device(effective_audio or "")
        return {
            "app_running": True,
            "version": VERSION,
            "ui_pid": os.getpid(),
            "capture": {
                "state": capture.state,
                "running": capture.is_running(),
                "user_paused": capture.user_paused,
                "video_device_configured": cfg.capture.video_device,
                "video_device_effective": effective_video,
                "audio_device_configured": cfg.capture.audio_device,
                "audio_device_effective": effective_audio,
                "pipeline_element": element,
                "pipewiresrc_installed": pipewiresrc_available(),
                "sink": "autoaudiosink",
                "usb_present": usb_status.usb_present,
                "signal_levels": None,
            },
            "adb": {
                "connected": self._window._adb.is_connected(),
            },
            "audio_audition": self._audition_status(),
        }

    def diagnostics_enumerate(self) -> dict[str, Any]:
        cfg = self._config().capture
        audio = [
            {"name": item.name, "description": item.description}
            for item in enumerate_audio_sources()
        ]
        video = [
            {"node": item.node, "description": item.description}
            for item in enumerate_v4l2_devices()
        ]
        auto_audio = resolve_audio_device(cfg)
        return {
            "audio_sources": audio,
            "video_devices": video,
            "auto_resolved_audio": auto_audio,
        }

    def diagnostics_audio_play(self, source: str) -> dict[str, Any]:
        name = (source or "").strip()
        if not name:
            queue = self._refresh_audio_queue()
            if not queue:
                raise ValueError("no audio sources available; run enumerate first")
            item = queue[0]
            name = item.name
            self._audio_queue = queue
            self._audio_queue_index = 0
        if not self._audition.start(name):
            raise RuntimeError(f"failed to start audition on {name}")
        return self._audition_status()

    def diagnostics_audio_stop(self) -> dict[str, Any]:
        self._audition.stop()
        return self._audition_status()

    def diagnostics_audio_test_next(self) -> dict[str, Any]:
        if not self._audio_queue:
            self._refresh_audio_queue()
        queue = self._audio_queue
        if not queue:
            raise ValueError("no audio sources in test queue")
        current = self._audition.device or (
            queue[self._audio_queue_index].name if queue else ""
        )
        next_index = next_queue_index(queue, current)
        if next_index is None:
            raise ValueError("audio test queue exhausted")
        self._audio_queue_index = next_index
        item = queue[next_index]
        if not self._audition.start(item.name):
            raise RuntimeError(f"failed to start audition on {item.name}")
        return {
            **self._audition_status(),
            "label": item.label,
            "index": next_index + 1,
            "total": len(queue),
        }

    def diagnostics_capture_restart(self) -> dict[str, Any]:
        capture = self._capture()
        invalidate_capture_cache()
        if capture.is_running():
            capture.resume_by_user()
        else:
            capture.start()
        return {"capture_state": capture.state}

    def shutdown(self) -> None:
        self._audition.stop()


_COMMANDS_REQUIRING_MAIN = frozenset(
    {
        "status",
        "audio-play",
        "audio-stop",
        "audio-test-next",
        "capture-restart",
    }
)


def dispatch_command(
    backend: DiagnosticsBackend | None,
    command: str,
    args: dict[str, Any],
    *,
    run_on_main: Callable[[Callable[[], Any]], Any] | None = None,
) -> dict[str, Any]:
    """Execute one diagnostics command and return the response data payload."""
    name = command.strip().lower()
    if name == "ping":
        return {"pong": True, "socket": str(DIAGNOSTICS_SOCKET)}

    if name == "logs":
        lines = args.get("lines", _DEFAULT_LOG_LINES)
        try:
            count = int(lines)
        except (TypeError, ValueError) as exc:
            raise ValueError("lines must be an integer") from exc
        return {"lines": diagnostics_log_lines(count)}

    if name == "enumerate":
        if backend is None:
            from .config import default_capture_config
            from .capture_device import resolve_audio_device as _resolve

            cfg = default_capture_config()
            return {
                "audio_sources": [
                    {"name": item.name, "description": item.description}
                    for item in enumerate_audio_sources()
                ],
                "video_devices": [
                    {"node": item.node, "description": item.description}
                    for item in enumerate_v4l2_devices()
                ],
                "auto_resolved_audio": _resolve(cfg),
                "app_running": is_ui_running(),
            }
        if run_on_main is not None and name in _COMMANDS_REQUIRING_MAIN:
            return run_on_main(backend.diagnostics_enumerate)
        return backend.diagnostics_enumerate()

    if backend is None:
        raise RuntimeError("Android TV Connect UI is not running")

    if name == "status":
        return _invoke_main(backend.diagnostics_status, run_on_main)
    if name == "audio-play":
        source = str(args.get("source", ""))
        return _invoke_main(lambda: backend.diagnostics_audio_play(source), run_on_main)
    if name == "audio-stop":
        return _invoke_main(backend.diagnostics_audio_stop, run_on_main)
    if name == "audio-test-next":
        return _invoke_main(backend.diagnostics_audio_test_next, run_on_main)
    if name == "capture-restart":
        return _invoke_main(backend.diagnostics_capture_restart, run_on_main)

    raise ValueError(f"unknown command: {command}")


def _invoke_main(
    func: Callable[[], dict[str, Any]],
    run_on_main: Callable[[Callable[[], Any]], Any] | None,
) -> dict[str, Any]:
    if run_on_main is None:
        return func()
    return run_on_main(func)


def handle_request_line(
    raw: str | bytes,
    backend: DiagnosticsBackend | None,
    *,
    run_on_main: Callable[[Callable[[], Any]], Any] | None = None,
) -> str:
    """Parse a request line and return a formatted response line."""
    try:
        request = parse_request(raw)
        data = dispatch_command(
            backend,
            request["command"],
            request["args"],
            run_on_main=run_on_main,
        )
        return format_response(ok=True, data=data)
    except Exception as exc:
        LOG.debug("diagnostics command failed: %s", exc)
        return format_response(ok=False, error=str(exc))


class DiagnosticsServer:
    """Background Unix domain socket server (never blocks the GTK main thread)."""

    def __init__(self, backend_factory: Callable[[], DiagnosticsBackend | None]) -> None:
        self._backend_factory = backend_factory
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None

    @property
    def socket_path(self) -> Path:
        return DIAGNOSTICS_SOCKET

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        install_diagnostics_logging()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._serve_forever,
            name="atv-diagnostics",
            daemon=True,
        )
        self._thread.start()
        LOG.info("Diagnostics socket listening at %s", DIAGNOSTICS_SOCKET)

    def stop(self) -> None:
        self._stop_event.set()
        sock = self._socket
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
        self._socket = None
        try:
            DIAGNOSTICS_SOCKET.unlink(missing_ok=True)
        except OSError:
            pass

    def _serve_forever(self) -> None:
        DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            DIAGNOSTICS_SOCKET.unlink(missing_ok=True)
        except OSError:
            pass

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket = server
        server.bind(str(DIAGNOSTICS_SOCKET))
        os.chmod(DIAGNOSTICS_SOCKET, 0o600)
        server.listen(8)
        server.settimeout(0.5)

        while not self._stop_event.is_set():
            try:
                conn, _addr = server.accept()
            except TimeoutError:
                continue
            except OSError:
                if self._stop_event.is_set():
                    break
                continue
            threading.Thread(
                target=self._handle_client,
                args=(conn,),
                name="atv-diagnostics-client",
                daemon=True,
            ).start()

        try:
            server.close()
        except OSError:
            pass

    def _handle_client(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(5.0)
            chunks: list[bytes] = []
            total = 0
            while total < _MAX_REQUEST_BYTES:
                try:
                    block = conn.recv(4096)
                except TimeoutError:
                    break
                if not block:
                    break
                chunks.append(block)
                total += len(block)
                if b"\n" in block:
                    break
            raw = b"".join(chunks)
            if not raw:
                conn.sendall(format_response(ok=False, error="empty request").encode("utf-8"))
                return

            backend = self._backend_factory()

            def run_on_main(func: Callable[[], Any]) -> Any:
                return _run_on_gtk_main(func)

            response = handle_request_line(
                raw,
                backend,
                run_on_main=run_on_main if backend is not None else None,
            )
            conn.sendall(response.encode("utf-8"))
        except OSError as exc:
            LOG.debug("diagnostics client I/O error: %s", exc)
        finally:
            try:
                conn.close()
            except OSError:
                pass
