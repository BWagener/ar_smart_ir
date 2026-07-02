#!/usr/bin/env python3
"""Merge multiple Tuya IR captures into a robust consensus code.

Inputs can be provided as:
  1) repeated --code arguments
  2) --input-file with one code per line
  3) --input-file containing JSON (array/object/string values)
  4) --input-file containing arbitrary text (base64-like tokens are extracted)

Outputs:
  - merged Tuya Base64
  - raw signed JSON timings (SmartIR Raw format)
  - Pronto hex
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path
from typing import Iterable, Any
import importlib.util


def _load_tuya_codec(repo_root: Path):
    codec_path = repo_root / "custom_components" / "ar_smart_ir" / "tuya_codec.py"
    spec = importlib.util.spec_from_file_location("tuya_codec", codec_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load tuya codec from {codec_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _extract_from_json(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, list):
        for item in value:
            out.extend(_extract_from_json(item))
    elif isinstance(value, dict):
        for item in value.values():
            out.extend(_extract_from_json(item))
    return out


def _extract_codes_from_text(text: str) -> list[str]:
    # Tuya codes are Base64-like strings; require at least 16 chars to avoid noise.
    return re.findall(r"[A-Za-z0-9+/=]{16,}", text)


def _read_codes_from_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(text)
        return _extract_from_json(parsed)
    except json.JSONDecodeError:
        return _extract_codes_from_text(text)


def _normalize_codes(codes: Iterable[str]) -> list[str]:
    cleaned = [c.strip() for c in codes if isinstance(c, str) and c.strip()]
    # De-duplicate while preserving order.
    deduped: list[str] = []
    seen: set[str] = set()
    for code in cleaned:
        if code not in seen:
            deduped.append(code)
            seen.add(code)
    return deduped


def _pulses_to_raw_json(pulses: list[int]) -> str:
    raw = [v if i % 2 == 0 else -v for i, v in enumerate(pulses)]
    return json.dumps(raw)


def _pulses_to_pronto(pulses: list[int], frequency_hz: int) -> str:
    if not pulses:
        raise ValueError("Cannot build Pronto from empty pulse list.")
    if frequency_hz <= 0:
        raise ValueError(f"Invalid frequency: {frequency_hz}")

    freq_mhz = frequency_hz / 1_000_000.0
    carrier_word = max(1, int(round(1.0 / (freq_mhz * 0.241246))))

    # Pronto stores mark/space pairs; drop trailing odd pulse if needed.
    pair_count = len(pulses) // 2
    words = [0x0000, carrier_word, pair_count, 0x0000]
    for i in range(pair_count * 2):
        ticks = max(1, int(round(abs(pulses[i]) * freq_mhz)))
        words.append(min(ticks, 0xFFFF))
    return " ".join(f"{w:04X}" for w in words)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Median-merge Tuya captures and output Tuya/Raw/Pronto formats."
    )
    parser.add_argument(
        "--code",
        action="append",
        default=[],
        help="A captured Tuya Base64 code (repeat for multiple captures).",
    )
    parser.add_argument(
        "--input-file",
        type=Path,
        help="File containing codes (json/lines/free text with base64 tokens).",
    )
    parser.add_argument(
        "--frequency",
        type=int,
        default=38000,
        help="Carrier frequency for Pronto output (default: 38000).",
    )
    parser.add_argument(
        "--keep-duplicates",
        action="store_true",
        help="Keep duplicate captures instead of de-duplicating.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    tuya_codec = _load_tuya_codec(repo_root)

    codes: list[str] = list(args.code)
    if args.input_file:
        codes.extend(_read_codes_from_file(args.input_file))

    if not args.keep_duplicates:
        codes = _normalize_codes(codes)
    else:
        codes = [c.strip() for c in codes if isinstance(c, str) and c.strip()]

    if len(codes) < 3:
        raise SystemExit("Need at least 3 captures to merge reliably.")

    decoded: list[list[int]] = []
    for i, code in enumerate(codes, start=1):
        try:
            decoded.append(tuya_codec.decode_tuya(code))
        except Exception as err:
            raise SystemExit(f"Capture #{i} is not a valid Tuya code: {err}") from err

    min_len = min(len(p) for p in decoded)
    aligned = [p[:min_len] for p in decoded]

    merged_pulses = [
        int(round(statistics.median(sample[idx] for sample in aligned)))
        for idx in range(min_len)
    ]

    merged_tuya = tuya_codec.encode_tuya(merged_pulses)
    merged_raw = _pulses_to_raw_json(merged_pulses)
    merged_pronto = _pulses_to_pronto(merged_pulses, args.frequency)

    print(f"captures_used: {len(codes)}")
    print(f"aligned_pulse_len: {min_len}")
    print()
    print("tuya:")
    print(merged_tuya)
    print()
    print("raw:")
    print(merged_raw)
    print()
    print("pronto:")
    print(merged_pronto)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
