#!/usr/bin/env python3
"""Rename media files after compression using filename and metadata dates.

The script scans recursively under a root directory, but only processes files
that live inside at least one folder whose name starts with a four-digit year.

For files whose names start with a date prefix:
- yyyy-mm-dd...
- yyyy-mm...

the script compares the filename date against the video's metadata creation
date. When the dates are close enough, the output name uses the metadata
timestamp. When they are far apart, or when metadata is unavailable, the output
name uses the filename date and a zero time.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO


SUPPORTED_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".m4v",
    ".jpg",
    ".jpeg",
    ".heic",
    ".heif",
    ".png",
    ".gif",
    ".tif",
    ".tiff",
    ".avif",
    ".webp",
}
METADATA_DATE_THRESHOLD_DAYS = 60
YEAR_FOLDER_RE = re.compile(r"^\d{4}")
FILENAME_DATE_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})(?:-(?P<day>\d{2}))?(?P<rest>.*)$"
)


@dataclass
class RenameItem:
    path: Path
    filename_date: datetime
    metadata_created_at: datetime | None
    use_metadata_timestamp: bool
    metadata_timestamp_text: str
    rename_reason: str


class RenameLog:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file: TextIO = self.log_path.open("w", encoding="utf-8", newline="")
        self._writer = csv.writer(self._file)
        self._writer.writerow(("original_filename", "metadata_timestamp", "renamed_filename"))

    def close(self) -> None:
        self._file.close()

    def write_row(self, original_filename: str, metadata_timestamp: str, renamed_filename: str) -> None:
        self._writer.writerow((original_filename, metadata_timestamp, renamed_filename))
        self._file.flush()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()

    if not root.exists() or not root.is_dir():
        print(f"[ERROR] root directory does not exist: {root}")
        return 1

    files = discover_media_files(root)
    log_path = root / f"rename_postprocessing_filenames_{timestamp_for_filename()}.csv"
    renamed = 0
    skipped = 0
    failed = 0
    missing_metadata = 0

    rename_log = RenameLog(log_path)
    items: list[RenameItem] = []
    try:
        for path in files:
            filename_date, _ = parse_filename_date(path.name)
            if filename_date is None:
                skipped += 1
                continue

            metadata_created_at = probe_creation_time(path)
            if metadata_created_at is None:
                missing_metadata += 1
                items.append(
                    RenameItem(
                        path=path,
                        filename_date=filename_date,
                        metadata_created_at=None,
                        use_metadata_timestamp=False,
                        metadata_timestamp_text="",
                        rename_reason="metadata unavailable",
                    )
                )
                continue

            difference_days = abs(
                (metadata_created_at.date() - filename_date.date()).days
            )
            use_metadata_timestamp = difference_days <= METADATA_DATE_THRESHOLD_DAYS
            items.append(
                RenameItem(
                    path=path,
                    filename_date=filename_date,
                    metadata_created_at=metadata_created_at,
                    use_metadata_timestamp=use_metadata_timestamp,
                    metadata_timestamp_text=format_datetime(metadata_created_at),
                    rename_reason=(
                        "used metadata timestamp"
                        if use_metadata_timestamp
                        else (
                            "filename date differed by more than "
                            f"{METADATA_DATE_THRESHOLD_DAYS} days"
                        )
                    ),
                )
            )

        items.sort(key=lambda item: item.path.as_posix().lower())

        for item in items:
            target_name = build_target_name(item)
            target_path = item.path.with_name(target_name)

            if target_path == item.path:
                skipped += 1
                print(f"[SKIP] {item.path.name}: already matches target format")
                continue

            if target_path.exists():
                skipped += 1
                print(f"[SKIP] {item.path.name}: target already exists: {target_path.name}")
                continue

            try:
                item.path.rename(target_path)
            except OSError as exc:
                failed += 1
                print(f"[ERROR] {item.path.name}: rename failed: {exc}")
                continue

            renamed += 1
            rename_log.write_row(item.path.name, item.metadata_timestamp_text, target_path.name)
            print(f"[RENAMED] {item.path.name} -> {target_path.name} ({item.rename_reason})")

        print(f"[LOG] Renamed-file log written to: {log_path}")
        print(
            f"[DONE] renamed={renamed} skipped={skipped} failed={failed} "
            f"missing_metadata={missing_metadata} from={len(files)} files"
        )
        return 1 if failed else 0
    finally:
        rename_log.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rename video files under year-named folders using filename and "
            "creation metadata dates."
        )
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        type=Path,
        help="Root directory to scan",
    )
    return parser.parse_args()


def discover_media_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if not is_under_year_folder(root, path):
            continue
        if not parse_filename_date(path.name)[0]:
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


def parse_filename_date(name: str) -> tuple[datetime | None, str]:
    match = FILENAME_DATE_RE.match(name)
    if match is None:
        return None, ""

    year = int(match.group("year"))
    month = int(match.group("month"))
    day_text = match.group("day")
    day = int(day_text) if day_text is not None else 1

    try:
        filename_date = datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None, ""

    return filename_date, match.group("rest")


def build_target_name(item: RenameItem) -> str:
    date_part = (
        item.metadata_created_at
        if item.use_metadata_timestamp and item.metadata_created_at is not None
        else item.filename_date
    )
    date_text = date_part.astimezone().strftime("%Y%m%d")
    time_text = (
        item.metadata_created_at.astimezone().strftime("%H%M%S")
        if item.use_metadata_timestamp and item.metadata_created_at is not None
        else "000000"
    )

    _, tail = parse_filename_date(item.path.name)
    tail = tail.lstrip(" _-")
    tail = tail.replace(" ", "_")

    if tail:
        return f"{date_text}_{time_text}_{tail}"
    return f"{date_text}_{time_text}{item.path.suffix}"


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
    return probe_creation_time_from_filesystem(path)


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


def probe_creation_time_from_filesystem(path: Path) -> datetime | None:
    try:
        stat_result = path.stat()
    except OSError:
        return None

    timestamp = getattr(stat_result, "st_birthtime", None)
    if timestamp is None:
        timestamp = stat_result.st_mtime

    if timestamp is None:
        return None

    return datetime.fromtimestamp(timestamp).astimezone()


def format_datetime(value: datetime) -> str:
    return value.astimezone().replace(microsecond=0).isoformat()


def timestamp_for_filename() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


if __name__ == "__main__":
    raise SystemExit(main())
