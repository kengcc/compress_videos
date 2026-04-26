#!/usr/bin/env python3
"""Report video files whose filename date differs from metadata creation time.

The script scans recursively under a root directory, but only reports files that
live inside at least one folder whose name starts with a four-digit year.

Filename dates are read from prefixes like:
- yyyy-mm-dd...
- yyyy-mm...

For yyyy-mm prefixes, the script compares against the first day of that month.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}
YEAR_FOLDER_RE = re.compile(r"^\d{4}")
FILENAME_DATE_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})(?:-(?P<day>\d{2}))?"
)


@dataclass
class DateCheckResult:
    path: Path
    filename_date: datetime
    metadata_created_at: datetime
    difference_days: float


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    threshold_days = args.threshold_days

    if not root.exists() or not root.is_dir():
        print(f"[ERROR] root directory does not exist: {root}")
        return 1

    files = discover_video_files(root)
    results: list[DateCheckResult] = []
    missing_metadata: list[Path] = []
    parsed_files = 0

    for path in files:
        filename_date = parse_filename_date(path.name)
        if filename_date is None:
            continue

        parsed_files += 1
        metadata_created_at = probe_creation_time(path)
        if metadata_created_at is None:
            missing_metadata.append(path)
            continue

        difference_days = abs(metadata_created_at - filename_date) / timedelta(days=1)
        if difference_days > threshold_days:
            results.append(
                DateCheckResult(
                    path=path,
                    filename_date=filename_date,
                    metadata_created_at=metadata_created_at,
                    difference_days=difference_days,
                )
            )

    results.sort(key=lambda item: (item.difference_days, item.path.as_posix()))

    if results:
        print(f"[MISMATCH] files with date difference > {threshold_days} days")
        for item in results:
            print(
                f"{item.path}\t"
                f"filename={item.filename_date.date().isoformat()}\t"
                f"metadata={format_datetime(item.metadata_created_at)}\t"
                f"diff_days={item.difference_days:.1f}"
            )
    else:
        print(f"[MISMATCH] none found over {threshold_days} days")

    if missing_metadata:
        print(f"[WARN] metadata not found for {len(missing_metadata)} file(s)")
        for path in missing_metadata:
            print(f"{path}\tmetadata=unavailable")

    print(
        f"[DONE] scanned={len(files)} parsed={parsed_files} "
        f"mismatches={len(results)} missing_metadata={len(missing_metadata)}"
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare filename dates against video creation metadata."
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        type=Path,
        help="Root directory to scan",
    )
    parser.add_argument(
        "--threshold-days",
        type=float,
        default=5,
        help="Report files whose filename date differs by more than this many days",
    )
    return parser.parse_args()


def discover_video_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if not is_under_year_folder(root, path):
            continue
        files.append(path)
    return sorted(files, key=lambda path: path.as_posix().lower())


def is_under_year_folder(root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False

    for part in relative.parts[:-1]:
        if YEAR_FOLDER_RE.match(part):
            return True
    return False


def parse_filename_date(name: str) -> datetime | None:
    match = FILENAME_DATE_RE.match(name)
    if match is None:
        return None

    year = int(match.group("year"))
    month = int(match.group("month"))
    day_text = match.group("day")
    day = int(day_text) if day_text is not None else 1

    try:
        return datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None


def probe_creation_time(path: Path) -> datetime | None:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
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
        parsed = parse_creation_time(value)
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


def parse_creation_time(value: str) -> datetime | None:
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone()


def format_datetime(value: datetime) -> str:
    return value.astimezone().replace(microsecond=0).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
