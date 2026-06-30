#!/usr/bin/env python3
"""Enumerate and probe PipeWire/Pulse audio capture sources for Android TV Connect."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass

sys.path.insert(0, ".")

from android_tv_connect.audio_source_test import build_audio_test_queue
from android_tv_connect.capture_device import resolve_audio_device
from android_tv_connect.config import default_capture_config
from android_tv_connect.media_enumeration import enumerate_audio_sources


@dataclass(frozen=True)
class ProbeResult:
    name: str
    label: str
    pipeline_ok: bool
    detail: str
    recommended: bool
    auto_resolved: bool


def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def _pactl_sources_short() -> list[tuple[str, str, str]]:
    """Return (index, name, state) for every pactl source including monitors."""
    rows: list[tuple[str, str, str]] = []
    proc = _run(["pactl", "list", "sources", "short"], timeout=3.0)
    if proc.returncode != 0:
        return rows
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        index = parts[0].strip()
        name = parts[1].strip()
        state = parts[-1].strip() if len(parts) >= 5 else ""
        rows.append((index, name, state))
    return rows


def _alsa_capture_devices() -> list[str]:
    proc = _run(["arecord", "-l"], timeout=3.0)
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip().startswith("card")]


def _probe_pulsesrc(source_name: str, *, seconds: float, buffers: int) -> tuple[bool, str]:
    cmd = [
        "gst-launch-1.0",
        "-q",
        "pulsesrc",
        f"device={source_name}",
        f"num-buffers={buffers}",
        "!",
        "audioconvert",
        "!",
        "fakesink",
        "sync=false",
    ]
    proc = _run(cmd, timeout=seconds + 2.0)
    if proc.returncode == 0:
        return True, f"opened and captured {buffers} buffers"
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()
    detail = tail[-1][:160] if tail else f"exit {proc.returncode}"
    return False, detail


def _is_recommended_capture_source(name: str, label: str) -> bool:
    blob = f"{name} {label}".lower()
    hints = ("macrosilicon", "ms2109", "usb3.0 capture", "usb3.0_capture")
    return any(hint in blob for hint in hints)


def probe_sources(
    *,
    seconds: float = 2.5,
    buffers: int = 80,
    skip_probe: bool = False,
) -> list[ProbeResult]:
    cfg = default_capture_config()
    app_sources = enumerate_audio_sources()
    auto_name = resolve_audio_device(cfg) or ""
    queue = build_audio_test_queue(
        app_sources,
        include_auto_resolved=auto_name or None,
    )

    results: list[ProbeResult] = []
    for item in queue:
        if skip_probe:
            ok, detail = True, "probe skipped"
        else:
            ok, detail = _probe_pulsesrc(item.name, seconds=seconds, buffers=buffers)
        results.append(
            ProbeResult(
                name=item.name,
                label=item.label,
                pipeline_ok=ok,
                detail=detail,
                recommended=_is_recommended_capture_source(item.name, item.label),
                auto_resolved=item.name == auto_name,
            )
        )
    return results


def print_report(results: list[ProbeResult]) -> int:
    cfg = default_capture_config()
    print("=== Android TV Connect audio source probe ===\n")
    print(f"USB capture dongle: {cfg.usb_vendor_product}")
    print(f"Configured audio_device: {cfg.audio_device}")
    print(f"Auto-resolved source: {resolve_audio_device(cfg) or '(none)'}\n")

    print("--- System: pactl list sources short ---")
    for index, name, state in _pactl_sources_short():
        kind = "monitor" if name.endswith(".monitor") else "input"
        print(f"  [{index}] {name} ({state or 'unknown'}) [{kind}]")

    print("\n--- System: ALSA capture hardware (arecord -l) ---")
    alsa = _alsa_capture_devices()
    if alsa:
        for line in alsa:
            print(f"  {line}")
    else:
        print("  (none)")

    print("\n--- App: enumerate_audio_sources() ---")
    for index, source in enumerate(enumerate_audio_sources(), start=1):
        print(f"  {index}. {source.description}")
        print(f"     name: {source.name}")

    print("\n--- Ordered line-by-line test queue ---")
    width = max((len(result.name) for result in results), default=10)
    failures = 0
    for index, result in enumerate(results, start=1):
        mark = "PASS" if result.pipeline_ok else "FAIL"
        if not result.pipeline_ok:
            failures += 1
        tags: list[str] = []
        if result.auto_resolved:
            tags.append("AUTO")
        if result.recommended:
            tags.append("RECOMMENDED")
        tag_text = f" [{' '.join(tags)}]" if tags else ""
        print(
            f"{index:>2}. [{mark}] {result.label}{tag_text}\n"
            f"     {result.name:<{width}}  {result.detail}"
        )

    recommended = [result for result in results if result.recommended]
    print("\n--- MacroSilicon MS2109 (534d:2109) guidance ---")
    if recommended:
        for result in recommended:
            status = "works" if result.pipeline_ok else "pipeline failed"
            print(f"  * {result.name} — {status}; use this for HDMI capture audio")
    else:
        print("  No MacroSilicon/MS2109 source found in enumeration.")

    print(f"\n{len(results) - failures}/{len(results)} sources passed pipeline probe")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enumerate and probe audio capture sources for Android TV Connect",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=2.5,
        help="Per-source GStreamer probe timeout (default: 2.5)",
    )
    parser.add_argument(
        "--buffers",
        type=int,
        default=80,
        help="num-buffers for pulsesrc probe (default: 80)",
    )
    parser.add_argument(
        "--skip-probe",
        action="store_true",
        help="List sources only; do not run gst-launch probes",
    )
    args = parser.parse_args()
    t0 = time.monotonic()
    results = probe_sources(
        seconds=args.seconds,
        buffers=args.buffers,
        skip_probe=args.skip_probe,
    )
    exit_code = print_report(results)
    print(f"\nCompleted in {(time.monotonic() - t0) * 1000:.0f}ms")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
