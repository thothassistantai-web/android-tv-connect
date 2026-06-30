#!/usr/bin/env python3
"""CLI client for Android TV Connect live diagnostics (Cursor agents).

Examples for Cursor shell agents
--------------------------------
Check whether the app is up and what capture is doing::

    python3 scripts/atv-diag.py status

List PipeWire/Pulse inputs and V4L2 nodes (works even when the UI is closed)::

    python3 scripts/atv-diag.py enumerate

Start continuous HDMI capture audition (in-app, does not freeze the GTK UI)::

    python3 scripts/atv-diag.py audio-play 'alsa_input.usb-MACROSILICON_USB3.0_Capture-02.analog-stereo'

Auto-pick the first / dongle source::

    python3 scripts/atv-diag.py audio-play

Cycle to the next source while audition keeps playing::

    python3 scripts/atv-diag.py audio-test-next

Stop audition::

    python3 scripts/atv-diag.py audio-stop

Hot-restart capture pipelines after device or settings changes::

    python3 scripts/atv-diag.py capture-restart

Tail recent app logs (ring buffer, requires running UI)::

    python3 scripts/atv-diag.py logs --lines 80

Socket
------
Default Unix socket: ~/.local/share/android-tv-connect/diagnostics.sock
(user-only permissions, local IPC only)

When the UI is not running, ``enumerate`` and ``ping`` still work; other commands
return an error. Start the app with ``atv-connect`` first.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from android_tv_connect.diagnostics_server import (
    DIAGNOSTICS_SOCKET,
    dispatch_command,
    format_response,
    handle_request_line,
    parse_request,
)

COMMANDS = (
    "ping",
    "status",
    "enumerate",
    "audio-play",
    "audio-stop",
    "audio-test-next",
    "capture-restart",
    "logs",
)


def send_request(command: str, args: dict[str, Any] | None = None, *, socket_path: Path | None = None) -> dict[str, Any]:
    """Send one JSON-line command to the diagnostics socket."""
    path = socket_path or DIAGNOSTICS_SOCKET
    payload = json.dumps({"command": command, "args": args or {}}) + "\n"

    if not path.exists():
        if command in ("ping", "enumerate", "logs"):
            raw = handle_request_line(payload, backend=None)
            return json.loads(raw)
        raise ConnectionError(
            f"diagnostics socket not found at {path}; start Android TV Connect (atv-connect) first"
        )

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(10.0)
        sock.connect(str(path))
        sock.sendall(payload.encode("utf-8"))
        chunks: list[bytes] = []
        while True:
            block = sock.recv(65536)
            if not block:
                break
            chunks.append(block)
            if b"\n" in block:
                break
    text = b"".join(chunks).decode("utf-8", errors="replace").strip()
    if not text:
        raise RuntimeError("empty response from diagnostics server")
    return json.loads(text)


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Android TV Connect live diagnostics (local Unix socket)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "command",
        choices=COMMANDS,
        help="diagnostics command to run",
    )
    parser.add_argument(
        "source",
        nargs="?",
        default="",
        help="audio source name for audio-play (optional; auto when omitted)",
    )
    parser.add_argument(
        "--lines",
        type=int,
        default=100,
        help="number of log lines for logs (default: 100)",
    )
    parser.add_argument(
        "--socket",
        type=Path,
        default=None,
        help=f"override socket path (default: {DIAGNOSTICS_SOCKET})",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="print the full JSON response envelope",
    )
    args = parser.parse_args(argv)

    cmd_args: dict[str, Any] = {}
    if args.command == "audio-play":
        cmd_args["source"] = args.source
    elif args.command == "logs":
        cmd_args["lines"] = args.lines

    try:
        response = send_request(args.command, cmd_args, socket_path=args.socket)
    except (ConnectionError, TimeoutError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.raw:
        _print_json(response)
    elif not response.get("ok"):
        print(response.get("error", "unknown error"), file=sys.stderr)
        return 1
    else:
        _print_json(response.get("data"))

    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
