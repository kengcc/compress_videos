#!/usr/bin/env python3
"""Rename files in ./input to date-prefixed names based on creation time."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}
DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{1,2}(?:-\d{2})?")
SPACED_DATE_PREFIX_RE = re.compile(
    r"^(?P<year>\d{4}) (?P<month>\d{2}) (?P<day>\d{2})(?P<rest>.*)$"
)
YEAR_SPACE_PREFIX_RE = re.compile(r"^\d{4} ")
NUMBERED_NAME_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2}) (?P<num>\d+)(?P<ext>\.[^.]+)$")


@dataclass
class VideoItem:
    path: Path
    created_at: datetime
    date_key: str


def main() -> int:
    input_dir = Path("input")
    if not input_dir.exists() or not input_dir.is_dir():
        print(f"[ERROR] input directory does not exist: {input_dir}")
        return 1

    files = discover_input_files(input_dir)
    used_numbers = existing_numbered_names(files)
    renamed = 0
    skipped = 0
    failed = 0

    # Rename rules:
    # - "yyyy mm dd..." -> "yyyy-mm-dd..." and keep the rest of the name.
    # - Names that already look date-prefixed with "yyyy-mm..." are skipped.
    # - Other names starting with "yyyy " are skipped.
    # - Everything else is renamed from the file creation time.
    normalized_targets: dict[Path, str] = {}
    creation_candidates: list[Path] = []
    for path in files:
        target_name = normalize_spaced_date_prefix(path.name)
        if target_name is not None:
            normalized_targets[path] = target_name
            continue
        if has_date_prefix(path.name) or starts_with_year_space_prefix(path.name):
            skipped += 1
            continue
        creation_candidates.append(path)

    for path, target_name in normalized_targets.items():
        target_path = path.with_name(target_name)

        if target_path.exists():
            print(f"[SKIP] {path.name}: target already exists: {target_path.name}")
            skipped += 1
            continue

        try:
            path.rename(target_path)
        except OSError as exc:
            print(f"[ERROR] {path.name}: rename failed: {exc}")
            failed += 1
            continue

        normalized_match = NUMBERED_NAME_RE.match(target_path.name)
        if normalized_match is not None:
            used_numbers.setdefault(normalized_match.group("date"), set()).add(
                int(normalized_match.group("num"))
            )

        renamed += 1
        print(f"[RENAMED] {path.name} -> {target_path.name}")

    if not creation_candidates:
        print(
            f"[DONE] renamed={renamed} skipped={skipped} failed={failed} "
            f"from={len(files)} files"
        )
        return 1 if failed else 0

    items = [build_video_item(path) for path in creation_candidates]
    items.sort(key=lambda item: (item.date_key, item.created_at, item.path.name.lower()))

    for item in items:
        next_number = next_available_number(
            used_numbers.setdefault(item.date_key, set())
        )
        target_name = f"{item.date_key} {next_number}{item.path.suffix}"
        target_path = item.path.with_name(target_name)

        while target_path.exists():
            next_number += 1
            target_name = f"{item.date_key} {next_number}{item.path.suffix}"
            target_path = item.path.with_name(target_name)

        try:
            item.path.rename(target_path)
        except OSError as exc:
            print(f"[ERROR] {item.path.name}: rename failed: {exc}")
            failed += 1
            continue

        used_numbers[item.date_key].add(next_number)
        renamed += 1
        print(
            f"[RENAMED] {item.path.name} -> {target_path.name} "
            f"({item.created_at.astimezone().date().isoformat()})"
        )

    print(
        f"[DONE] renamed={renamed} skipped={skipped} failed={failed} "
        f"from={len(files)} files"
    )
    return 1 if failed else 0


def discover_input_files(input_dir: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in input_dir.iterdir()
            if path.is_file()
            and path.name != ".DS_Store"
            and path.suffix.lower() in SUPPORTED_EXTENSIONS
        ),
        key=lambda path: path.name.lower(),
    )


def has_date_prefix(name: str) -> bool:
    return bool(DATE_PREFIX_RE.match(name))


def normalize_spaced_date_prefix(name: str) -> str | None:
    match = SPACED_DATE_PREFIX_RE.match(name)
    if match is None:
        return None
    return f"{match.group('year')}-{match.group('month')}-{match.group('day')}{match.group('rest')}"


def starts_with_year_space_prefix(name: str) -> bool:
    return bool(YEAR_SPACE_PREFIX_RE.match(name))


def build_video_item(path: Path) -> VideoItem:
    created_at = probe_creation_time(path)
    return VideoItem(path=path, created_at=created_at, date_key=created_at.date().isoformat())


def probe_creation_time(path: Path) -> datetime:
    creation_time = probe_ffprobe_creation_time(path)
    if creation_time is not None:
        return creation_time.astimezone()

    stat_result = path.stat()
    timestamp = getattr(stat_result, "st_birthtime", stat_result.st_mtime)
    return datetime.fromtimestamp(timestamp).astimezone()


def probe_ffprobe_creation_time(path: Path) -> datetime | None:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_entries",
        "format_tags=creation_time:stream_tags=creation_time",
        str(path),
    ]
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None

    try:
        data: dict[str, Any] = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None

    for value in iter_creation_time_values(data):
        parsed = parse_datetime(value)
        if parsed is not None:
            return parsed
    return None


def iter_creation_time_values(data: dict[str, Any]) -> list[str]:
    values: list[str] = []

    format_data = data.get("format")
    if isinstance(format_data, dict):
        tags = format_data.get("tags")
        if isinstance(tags, dict):
            value = tags.get("creation_time")
            if isinstance(value, str) and value.strip():
                values.append(value)

    streams = data.get("streams")
    if isinstance(streams, list):
        for stream in streams:
            if not isinstance(stream, dict):
                continue
            tags = stream.get("tags")
            if not isinstance(tags, dict):
                continue
            value = tags.get("creation_time")
            if isinstance(value, str) and value.strip():
                values.append(value)

    return values


def parse_datetime(value: str) -> datetime | None:
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def existing_numbered_names(files: list[Path]) -> dict[str, set[int]]:
    used: dict[str, set[int]] = {}
    for path in files:
        match = NUMBERED_NAME_RE.match(path.name)
        if match is None:
            continue
        used.setdefault(match.group("date"), set()).add(int(match.group("num")))
    return used


def next_available_number(used: set[int]) -> int:
    number = 1
    while number in used:
        number += 1
    return number


if __name__ == "__main__":
    raise SystemExit(main())
