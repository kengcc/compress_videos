#!/usr/bin/env python3
"""Compress videos from an input folder into an output folder using ffmpeg."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "input_dir": "./input",
    "output_dir": "./output",
    "min_file_size_mb": None,
    "min_duration_seconds": None,
    "max_height": 1080,
    "crf": 23,
    "preset": "medium",
    "audio_mode": "aac",
    "audio_bitrate": "128k",
    "skip_if_codec": [],
    "skip_codec_default_max_size_mb": 20,
    "skip_existing_outputs": True,
    "enable_smart_skip": True,
    "smart_skip_short_duration_seconds": 5,
    "smart_skip_low_resolution_height": 360,
    "smart_skip_low_resolution_size_mb": 5,
    "smart_skip_480p_height": 480,
    "smart_skip_480p_max_bitrate": "500k",
    "smart_skip_720p_height": 720,
    "smart_skip_720p_max_size_mb": 8,
    "smart_skip_720p_max_bitrate": "1000k",
    "smart_skip_1080p_height": 1080,
    "smart_skip_1080p_max_size_mb": 12,
    "smart_skip_1080p_max_bitrate": "1800k",
    "enable_sample_preflight": False,
    "sample_preflight_codecs": ["hevc"],
    "sample_preflight_min_duration_seconds": 5,
    "sample_preflight_seconds": 8,
    "sample_preflight_min_ratio": 0.98,
    "parallel_jobs": 1,
    "error_log_line_count": 8,
    "supported_extensions": [".mp4", ".mov", ".avi", ".mkv", ".m4v"],
}


class ConfigError(ValueError):
    """Raised when config.json is invalid."""


class ProbeError(RuntimeError):
    """Raised when ffprobe cannot read useful video metadata."""


class EncodeError(RuntimeError):
    """Raised when ffmpeg cannot produce a valid compressed file."""


@dataclass
class ProcessResult:
    status: str
    path: Path
    reason: str = ""
    input_size: int | None = None
    output_size: int | None = None


class RunLogger:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.log_path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

    def close(self) -> None:
        self._file.close()

    def log(self, message: str) -> None:
        timestamp = datetime.now().isoformat(timespec="seconds")
        line = f"{timestamp} {message}"
        with self._lock:
            print(line, flush=True)
            self._file.write(f"{line}\n")
            self._file.flush()


LOGGER: RunLogger | None = None


def main() -> int:
    results: list[ProcessResult] = []

    try:
        config = load_config(Path("config.json"))
        config = validate_config(config)
        log_path = start_logging(config["output_dir"])
        log(f"[LOG] Writing run log to {log_path}")
        ensure_tools_available()
        ensure_directories(config)
        files = discover_video_files(
            config["input_dir"], config["supported_extensions"]
        )
    except (ConfigError, OSError, RuntimeError) as exc:
        log(f"[ERROR] {exc}")
        close_logging()
        return 1

    log(f"[SCAN] Found {len(files)} supported files")

    try:
        results = process_files(files, config)
    except KeyboardInterrupt:
        log("\n[ERROR] Interrupted; completed outputs were left intact")
        log_summary(results)
        close_logging()
        return 130

    log_summary(results)
    close_logging()
    counts = count_results(results)
    return 1 if counts["failed"] else 0


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return dict(DEFAULT_CONFIG)

    try:
        with config_path.open("r", encoding="utf-8") as config_file:
            user_config = json.load(config_file)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{config_path} contains invalid JSON: {exc.msg} at line {exc.lineno}"
        ) from exc

    if not isinstance(user_config, dict):
        raise ConfigError(f"{config_path} must contain a JSON object")

    return merge_config(user_config)


def merge_config(user_config: dict[str, Any]) -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    config.update(user_config)
    return config


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    validated = dict(config)

    input_dir = require_string(validated, "input_dir")
    output_dir = require_string(validated, "output_dir")
    validated["input_dir"] = Path(input_dir)
    validated["output_dir"] = Path(output_dir)

    if validated["input_dir"].resolve() == validated["output_dir"].resolve():
        raise ConfigError("input_dir and output_dir must be different directories")

    validated["min_file_size_mb"] = validate_optional_positive_number(
        validated, "min_file_size_mb"
    )
    validated["min_duration_seconds"] = validate_optional_positive_number(
        validated, "min_duration_seconds"
    )
    validated["max_height"] = validate_positive_int(validated, "max_height")
    validated["crf"] = validate_int_range(validated, "crf", 0, 51)
    validated["preset"] = require_string(validated, "preset")
    validated["audio_mode"] = validate_choice(
        validated, "audio_mode", {"auto", "copy", "aac"}
    )
    validated["audio_bitrate"] = require_string(validated, "audio_bitrate")
    validated["audio_bitrate_bps"] = parse_bitrate(
        validated["audio_bitrate"], "audio_bitrate"
    )
    validated["skip_if_codec"] = validate_string_list(validated, "skip_if_codec")
    validated["skip_codec_default_max_size_mb"] = validate_optional_positive_number(
        validated, "skip_codec_default_max_size_mb"
    )
    validated["skip_existing_outputs"] = validate_bool(
        validated, "skip_existing_outputs"
    )
    validated["enable_smart_skip"] = validate_bool(validated, "enable_smart_skip")
    validated["smart_skip_short_duration_seconds"] = (
        validate_optional_positive_number(
            validated, "smart_skip_short_duration_seconds"
        )
    )
    validated["smart_skip_low_resolution_height"] = validate_positive_int(
        validated, "smart_skip_low_resolution_height"
    )
    validated["smart_skip_low_resolution_size_mb"] = (
        validate_optional_positive_number(validated, "smart_skip_low_resolution_size_mb")
    )
    validated["smart_skip_480p_height"] = validate_positive_int(
        validated, "smart_skip_480p_height"
    )
    validated["smart_skip_480p_max_bitrate_bps"] = validate_optional_bitrate(
        validated, "smart_skip_480p_max_bitrate"
    )
    validated["smart_skip_720p_height"] = validate_positive_int(
        validated, "smart_skip_720p_height"
    )
    validated["smart_skip_720p_max_size_mb"] = validate_optional_positive_number(
        validated, "smart_skip_720p_max_size_mb"
    )
    validated["smart_skip_720p_max_bitrate_bps"] = validate_optional_bitrate(
        validated, "smart_skip_720p_max_bitrate"
    )
    validated["smart_skip_1080p_height"] = validate_positive_int(
        validated, "smart_skip_1080p_height"
    )
    validated["smart_skip_1080p_max_size_mb"] = validate_optional_positive_number(
        validated, "smart_skip_1080p_max_size_mb"
    )
    validated["smart_skip_1080p_max_bitrate_bps"] = validate_optional_bitrate(
        validated, "smart_skip_1080p_max_bitrate"
    )
    validated["enable_sample_preflight"] = validate_bool(
        validated, "enable_sample_preflight"
    )
    validated["sample_preflight_codecs"] = validate_string_list(
        validated, "sample_preflight_codecs"
    )
    validated["sample_preflight_min_duration_seconds"] = (
        validate_optional_positive_number(
            validated, "sample_preflight_min_duration_seconds"
        )
    )
    validated["sample_preflight_seconds"] = validate_optional_positive_number(
        validated, "sample_preflight_seconds"
    )
    if validated["sample_preflight_seconds"] is None:
        raise ConfigError("sample_preflight_seconds must be a positive number")
    validated["sample_preflight_min_ratio"] = validate_optional_positive_number(
        validated, "sample_preflight_min_ratio"
    )
    if validated["sample_preflight_min_ratio"] is None:
        raise ConfigError("sample_preflight_min_ratio must be a positive number")
    if validated["sample_preflight_min_ratio"] >= 1:
        raise ConfigError("sample_preflight_min_ratio must be less than 1")
    validated["parallel_jobs"] = validate_positive_int(validated, "parallel_jobs")
    validated["error_log_line_count"] = validate_positive_int(
        validated, "error_log_line_count"
    )
    validated["supported_extensions"] = validate_extensions(
        validated, "supported_extensions"
    )

    return validated


def require_string(config: dict[str, Any], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string")
    return value


def validate_optional_positive_number(
    config: dict[str, Any], key: str
) -> float | None:
    value = config.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{key} must be a positive number or null")
    if value <= 0:
        raise ConfigError(f"{key} must be greater than zero when set")
    return float(value)


def validate_positive_int(config: dict[str, Any], key: str) -> int:
    value = config.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} must be a positive integer")
    if value <= 0:
        raise ConfigError(f"{key} must be greater than zero")
    return value


def validate_int_range(
    config: dict[str, Any], key: str, minimum: int, maximum: int
) -> int:
    value = config.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} must be an integer")
    if not minimum <= value <= maximum:
        raise ConfigError(f"{key} must be between {minimum} and {maximum}")
    return value


def validate_string_list(config: dict[str, Any], key: str) -> list[str]:
    value = config.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{key} must be a list of strings")
    return [item.lower() for item in value]


def validate_choice(config: dict[str, Any], key: str, choices: set[str]) -> str:
    value = require_string(config, key).lower()
    if value not in choices:
        allowed = ", ".join(sorted(choices))
        raise ConfigError(f"{key} must be one of: {allowed}")
    return value


def validate_bool(config: dict[str, Any], key: str) -> bool:
    value = config.get(key)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be true or false")
    return value


def parse_bitrate(value: str, key: str) -> int:
    text = value.strip().lower()
    multipliers = {"k": 1_000, "m": 1_000_000}
    multiplier = multipliers.get(text[-1], 1)
    number_text = text[:-1] if text[-1] in multipliers else text

    try:
        bitrate = int(float(number_text) * multiplier)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a bitrate like 128k") from exc

    if bitrate <= 0:
        raise ConfigError(f"{key} must be greater than zero")
    return bitrate


def validate_optional_bitrate(config: dict[str, Any], key: str) -> int | None:
    value = config.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a bitrate string like 1200k or null")
    return parse_bitrate(value, key)


def validate_extensions(config: dict[str, Any], key: str) -> set[str]:
    extensions = validate_string_list(config, key)
    if not extensions:
        raise ConfigError(f"{key} must not be empty")

    normalized = set()
    for extension in extensions:
        extension = extension.strip().lower()
        if not extension:
            raise ConfigError(f"{key} must not contain empty extensions")
        if not extension.startswith("."):
            extension = f".{extension}"
        normalized.add(extension)
    return normalized


def ensure_tools_available() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        tools = ", ".join(missing)
        raise RuntimeError(
            f"Missing required tool(s): {tools}. Install ffmpeg and ffprobe first."
        )


def ensure_directories(config: dict[str, Any]) -> None:
    input_dir = config["input_dir"]
    output_dir = config["output_dir"]

    if not input_dir.exists() or not input_dir.is_dir():
        raise RuntimeError(f"Input directory does not exist: {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)


def discover_video_files(input_dir: Path, extensions: set[str]) -> list[Path]:
    return sorted(
        (
            path
            for path in input_dir.iterdir()
            if path.is_file() and path.suffix.lower() in extensions
        ),
        key=lambda path: path.name.lower(),
    )


def process_files(files: list[Path], config: dict[str, Any]) -> list[ProcessResult]:
    parallel_jobs = config["parallel_jobs"]
    if parallel_jobs <= 1 or len(files) <= 1:
        return [process_file(input_path, config) for input_path in files]

    log(f"[PARALLEL] Processing with {parallel_jobs} workers")
    with ThreadPoolExecutor(max_workers=parallel_jobs) as executor:
        return list(
            executor.map(lambda input_path: process_file(input_path, config), files)
        )


def process_file(input_path: Path, config: dict[str, Any]) -> ProcessResult:
    final_output_path = config["output_dir"] / input_path.name
    temp_output_path = config["output_dir"] / f"{input_path.stem}.tmp{input_path.suffix}"
    sample_output_path = (
        config["output_dir"] / f"{input_path.stem}.sample{input_path.suffix}"
    )

    try:
        if should_skip_existing_output(input_path, final_output_path, config):
            log(f"[SKIP] {input_path.name}: existing output found")
            return ProcessResult(
                "skipped",
                input_path,
                "existing output found",
                input_path.stat().st_size,
                final_output_path.stat().st_size,
            )

        remove_if_exists(temp_output_path)
        remove_if_exists(sample_output_path)
        metadata = probe_video(input_path)
        log_probe(input_path, metadata)

        should_encode, reason = should_compress(input_path, metadata, config)
        if not should_encode:
            copy_original(input_path, final_output_path)
            log(f"[SKIP] {input_path.name}: {reason}; copied original")
            return ProcessResult(
                "skipped",
                input_path,
                reason,
                input_path.stat().st_size,
                final_output_path.stat().st_size,
            )

        preflight_reason = run_sample_preflight_if_enabled(
            input_path, sample_output_path, metadata, config
        )
        if preflight_reason is not None:
            copy_original(input_path, final_output_path)
            log(f"[SKIP] {input_path.name}: {preflight_reason}; copied original")
            return ProcessResult(
                "skipped",
                input_path,
                preflight_reason,
                input_path.stat().st_size,
                final_output_path.stat().st_size,
            )

        scale_note = ""
        if metadata["height"] and metadata["height"] > config["max_height"]:
            scale_note = f", scaling to {config['max_height']}p"

        log(
            f"[COMPRESS] {input_path.name}: libx265 "
            f"crf={config['crf']} preset={config['preset']} "
            f"{describe_audio_mode(metadata, config)}{scale_note}"
        )
        compress_video(input_path, temp_output_path, metadata, config)
        result = finalize_output(input_path, temp_output_path, final_output_path)
        return result
    except (ProbeError, EncodeError) as exc:
        return fallback_copy(input_path, final_output_path, temp_output_path, str(exc))
    except OSError as exc:
        log(f"[ERROR] {input_path.name}: {exc}")
        remove_if_exists(temp_output_path)
        remove_if_exists(sample_output_path)
        return ProcessResult("failed", input_path, str(exc))
    finally:
        remove_if_exists(sample_output_path)


def should_skip_existing_output(
    input_path: Path, final_output_path: Path, config: dict[str, Any]
) -> bool:
    if not config["skip_existing_outputs"]:
        return False
    if not final_output_path.exists() or final_output_path.stat().st_size <= 0:
        return False
    return final_output_path.stat().st_mtime >= input_path.stat().st_mtime


def probe_video(path: Path) -> dict[str, Any]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "ffprobe failed"
        raise ProbeError(detail)

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError("ffprobe returned invalid JSON") from exc

    return parse_ffprobe_metadata(data)


def parse_ffprobe_metadata(data: dict[str, Any]) -> dict[str, Any]:
    streams = data.get("streams")
    if not isinstance(streams, list):
        raise ProbeError("ffprobe returned no stream data")

    video_stream = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        None,
    )
    if video_stream is None:
        raise ProbeError("no video stream found")

    audio_stream = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "audio"
        ),
        None,
    )

    format_data = data.get("format")
    if not isinstance(format_data, dict):
        format_data = {}

    codec = str(video_stream.get("codec_name") or "").lower()
    width = parse_optional_int(video_stream.get("width"))
    height = parse_optional_int(video_stream.get("height"))
    duration = parse_optional_float(
        video_stream.get("duration"), format_data.get("duration")
    )
    bit_rate = parse_optional_int(
        video_stream.get("bit_rate"), format_data.get("bit_rate")
    )
    audio_bit_rate = None
    if audio_stream is not None:
        audio_bit_rate = parse_optional_int(audio_stream.get("bit_rate"))

    if not codec:
        raise ProbeError("video codec is unknown")
    if width is None or height is None:
        raise ProbeError("video resolution is unknown")

    return {
        "codec": codec,
        "width": width,
        "height": height,
        "duration": duration,
        "bit_rate": bit_rate,
        "audio_bit_rate": audio_bit_rate,
        "has_audio_stream": audio_stream is not None,
    }


def parse_optional_float(*values: Any) -> float | None:
    for value in values:
        if value in (None, "N/A"):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def parse_optional_int(*values: Any) -> int | None:
    for value in values:
        if value in (None, "N/A"):
            continue
        try:
            return int(float(value))
        except (TypeError, ValueError):
            continue
    return None


def should_compress(
    path: Path, metadata: dict[str, Any], config: dict[str, Any]
) -> tuple[bool, str]:
    if not config["enable_smart_skip"]:
        return True, "compression enabled for all inputs"

    file_size_mb = path.stat().st_size / (1024 * 1024)
    min_file_size_mb = config["min_file_size_mb"]
    min_duration_seconds = config["min_duration_seconds"]
    duration = metadata["duration"]
    codec = metadata["codec"]

    if min_file_size_mb is not None and file_size_mb < min_file_size_mb:
        return False, f"below min file size ({human_size(path.stat().st_size)})"

    if (
        min_duration_seconds is not None
        and duration is not None
        and duration < min_duration_seconds
    ):
        return False, f"below min duration ({duration:.1f}s)"

    reason = should_skip_by_resolution_and_bitrate(metadata, file_size_mb, config)
    if reason is not None:
        return False, reason

    if codec in config["skip_if_codec"]:
        if min_file_size_mb is not None and file_size_mb <= min_file_size_mb:
            return False, f"codec {codec} and file is already small enough"
        default_size_mb = config["skip_codec_default_max_size_mb"]
        if default_size_mb is not None and file_size_mb < default_size_mb:
            return False, f"codec {codec} and file is already small enough"

    return True, "compression enabled for all inputs"


def should_skip_by_resolution_and_bitrate(
    metadata: dict[str, Any], file_size_mb: float, config: dict[str, Any]
) -> str | None:
    height = metadata["height"]
    bit_rate = metadata["bit_rate"] or 0
    duration = metadata["duration"]
    file_size_bytes = int(file_size_mb * 1024 * 1024)
    short_duration = config["smart_skip_short_duration_seconds"]
    low_resolution_height = config["smart_skip_low_resolution_height"]
    low_resolution_size_mb = config["smart_skip_low_resolution_size_mb"]
    max_480p_height = config["smart_skip_480p_height"]
    max_480p_bitrate = config["smart_skip_480p_max_bitrate_bps"]
    max_720p_height = config["smart_skip_720p_height"]
    max_720p_size_mb = config["smart_skip_720p_max_size_mb"]
    max_720p_bitrate = config["smart_skip_720p_max_bitrate_bps"]
    max_1080p_height = config["smart_skip_1080p_height"]
    max_1080p_size_mb = config["smart_skip_1080p_max_size_mb"]
    max_1080p_bitrate = config["smart_skip_1080p_max_bitrate_bps"]

    if (
        short_duration is not None
        and duration is not None
        and duration < short_duration
    ):
        return f"short clip ({duration:.1f}s)"

    if (
        low_resolution_size_mb is not None
        and height <= low_resolution_height
        and file_size_mb < low_resolution_size_mb
    ):
        return f"low resolution and already small ({human_size(file_size_bytes)})"

    if height <= max_480p_height and bit_rate and max_480p_bitrate is not None:
        if bit_rate < max_480p_bitrate:
            return f"480p bitrate already low ({bit_rate / 1_000_000:.2f} Mbps)"

    if (
        height <= max_720p_height
        and max_720p_size_mb is not None
        and max_720p_bitrate is not None
        and file_size_mb < max_720p_size_mb
        and bit_rate
        and bit_rate < max_720p_bitrate
    ):
        return f"720p bitrate already low ({bit_rate / 1_000_000:.2f} Mbps)"

    if (
        height <= max_1080p_height
        and max_1080p_size_mb is not None
        and max_1080p_bitrate is not None
        and file_size_mb < max_1080p_size_mb
        and bit_rate
        and bit_rate < max_1080p_bitrate
    ):
        return f"1080p bitrate already reasonable ({bit_rate / 1_000_000:.2f} Mbps)"

    return None


def run_sample_preflight_if_enabled(
    input_path: Path,
    sample_output_path: Path,
    metadata: dict[str, Any],
    config: dict[str, Any],
) -> str | None:
    if not config["enable_sample_preflight"]:
        return None
    if not should_run_sample_preflight(metadata, config):
        return None

    duration = metadata["duration"]
    sample_seconds = min(config["sample_preflight_seconds"], duration)
    if sample_seconds <= 0:
        return None

    compress_sample(input_path, sample_output_path, sample_seconds, metadata, config)
    sample_size = sample_output_path.stat().st_size
    projected_size = int(sample_size * (duration / sample_seconds))
    input_size = input_path.stat().st_size
    ratio = projected_size / input_size
    log(
        f"[PREFLIGHT] {input_path.name}: sample projects "
        f"{human_size(projected_size)} from {human_size(input_size)}"
    )

    if ratio >= config["sample_preflight_min_ratio"]:
        return (
            "sample preflight did not predict useful savings "
            f"({ratio:.0%} of original)"
        )

    return None


def should_run_sample_preflight(metadata: dict[str, Any], config: dict[str, Any]) -> bool:
    duration = metadata["duration"]
    if duration is None or duration <= 0:
        return False
    min_duration = config["sample_preflight_min_duration_seconds"]
    if min_duration is not None and duration <= min_duration:
        return False
    return metadata["codec"] in config["sample_preflight_codecs"]


def compress_sample(
    input_path: Path,
    sample_output_path: Path,
    sample_seconds: float,
    metadata: dict[str, Any],
    config: dict[str, Any],
) -> None:
    command = build_ffmpeg_command(input_path, sample_output_path, metadata, config)
    command.insert(-1, "-t")
    command.insert(-1, f"{sample_seconds:.3f}")
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (
            last_stderr_lines(result.stderr, config["error_log_line_count"])
            or "sample ffmpeg failed"
        )
        raise EncodeError(f"sample preflight failed: {detail}")
    if not sample_output_path.exists() or sample_output_path.stat().st_size <= 0:
        raise EncodeError("sample preflight did not produce a valid output file")


def build_ffmpeg_command(
    input_path: Path,
    output_path: Path,
    metadata: dict[str, Any],
    config: dict[str, Any],
) -> list[str]:
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "libx265",
        "-crf",
        str(config["crf"]),
        "-preset",
        config["preset"],
        "-tag:v",
        "hvc1",
    ]

    if metadata["height"] > config["max_height"]:
        command.extend(["-vf", f"scale=-2:{config['max_height']}"])

    if should_reencode_audio(metadata, config):
        command.extend(["-c:a", "aac", "-b:a", config["audio_bitrate"]])
    else:
        command.extend(["-c:a", "copy"])

    command.append(str(output_path))
    return command


def should_reencode_audio(metadata: dict[str, Any], config: dict[str, Any]) -> bool:
    audio_mode = config["audio_mode"]
    if audio_mode == "copy":
        return False
    if audio_mode not in {"aac", "auto"}:
        return False

    if not metadata["has_audio_stream"]:
        return False

    audio_bit_rate = metadata["audio_bit_rate"]
    if audio_bit_rate is None:
        return False
    return audio_bit_rate > config["audio_bitrate_bps"]


def describe_audio_mode(metadata: dict[str, Any], config: dict[str, Any]) -> str:
    audio_mode = config["audio_mode"]
    if audio_mode == "copy":
        return "audio=copy"
    if should_reencode_audio(metadata, config):
        return f"audio=aac {config['audio_bitrate']}"

    if not metadata["has_audio_stream"]:
        return "audio=copy (no audio stream)"

    audio_bit_rate = metadata["audio_bit_rate"]
    if audio_bit_rate is None:
        return "audio=copy (unknown bitrate)"
    return f"audio=copy ({human_bitrate(audio_bit_rate)} <= {config['audio_bitrate']})"


def human_bitrate(bits_per_second: int) -> str:
    if bits_per_second >= 1_000_000:
        return f"{bits_per_second / 1_000_000:.1f} Mbps"
    if bits_per_second >= 1_000:
        return f"{bits_per_second / 1_000:.0f} kbps"
    return f"{bits_per_second} bps"


def compress_video(
    input_path: Path,
    temp_output_path: Path,
    metadata: dict[str, Any],
    config: dict[str, Any],
) -> None:
    command = build_ffmpeg_command(input_path, temp_output_path, metadata, config)
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (
            last_stderr_lines(result.stderr, config["error_log_line_count"])
            or "ffmpeg failed"
        )
        raise EncodeError(detail)
    if not temp_output_path.exists() or temp_output_path.stat().st_size <= 0:
        raise EncodeError("ffmpeg did not produce a valid output file")


def copy_original(input_path: Path, final_output_path: Path) -> None:
    final_output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(input_path, final_output_path)


def finalize_output(
    input_path: Path,
    temp_output_path: Path,
    final_output_path: Path,
) -> ProcessResult:
    input_size = input_path.stat().st_size
    output_size = temp_output_path.stat().st_size

    if output_size < input_size:
        os.replace(temp_output_path, final_output_path)
        log(
            f"[KEEP] {input_path.name}: "
            f"{human_size(input_size)} -> {human_size(output_size)}"
        )
        return ProcessResult(
            "compressed",
            input_path,
            "compressed output is smaller",
            input_size,
            output_size,
        )

    remove_if_exists(temp_output_path)
    copy_original(input_path, final_output_path)
    log(
        f"[FALLBACK] {input_path.name}: compressed file was larger "
        f"({human_size(input_size)} -> {human_size(output_size)}); copied original"
    )
    return ProcessResult(
        "retained",
        input_path,
        "compressed output was larger; copied original",
        input_size,
        output_size,
    )


def fallback_copy(
    input_path: Path,
    final_output_path: Path,
    temp_output_path: Path,
    reason: str,
) -> ProcessResult:
    remove_if_exists(temp_output_path)
    try:
        copy_original(input_path, final_output_path)
    except OSError as exc:
        log(f"[ERROR] {input_path.name}: {reason}; copy failed: {exc}")
        return ProcessResult("failed", input_path, f"{reason}; copy failed: {exc}")

    log(f"[ERROR] {input_path.name}: {reason}; copied original")
    return ProcessResult(
        "failed",
        input_path,
        f"{reason}; copied original",
        input_path.stat().st_size,
        final_output_path.stat().st_size,
    )


def remove_if_exists(path: Path) -> None:
    if path.exists():
        path.unlink()


def log_probe(path: Path, metadata: dict[str, Any]) -> None:
    duration = metadata["duration"]
    duration_text = "unknown duration" if duration is None else f"{duration:.1f}s"
    log(
        f"[PROBE] {path}: {metadata['codec']}, "
        f"{metadata['width']}x{metadata['height']}, "
        f"{duration_text}, {human_size(path.stat().st_size)}"
    )


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def last_stderr_lines(stderr: str, line_count: int) -> str:
    lines = [line for line in stderr.strip().splitlines() if line.strip()]
    return "\n".join(lines[-line_count:])


def start_logging(output_dir: Path) -> Path:
    global LOGGER
    log_path = output_dir / "logs" / f"compress_videos_{timestamp_for_filename()}.log"
    LOGGER = RunLogger(log_path)
    return log_path


def close_logging() -> None:
    global LOGGER
    if LOGGER is not None:
        LOGGER.close()
        LOGGER = None


def timestamp_for_filename() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def count_results(results: list[ProcessResult]) -> dict[str, int]:
    counts = {"compressed": 0, "skipped": 0, "retained": 0, "failed": 0}
    for result in results:
        counts[result.status] += 1
    return counts


def log_summary(results: list[ProcessResult]) -> None:
    counts = count_results(results)
    log(
        "[DONE] "
        f"compressed={counts['compressed']} "
        f"skipped={counts['skipped']} "
        f"retained={counts['retained']} "
        f"failed={counts['failed']}"
    )
    rows = [
        (
            result.path.name,
            summarize_status(result),
            format_size(result.input_size),
            format_size(result.output_size),
        )
        for result in results
    ]
    log("")
    log_summary_table(rows)
    log("")
    log_total_sizes(results)


def summarize_status(result: ProcessResult) -> str:
    return {
        "compressed": "compress",
        "skipped": "skip",
        "retained": "keep",
        "failed": "fail",
    }.get(result.status, result.status)


def format_size(size: int | None) -> str:
    if size is None:
        return "-"
    return human_size(size)


def log_total_sizes(results: list[ProcessResult]) -> None:
    total_input_size = sum(
        result.input_size for result in results if result.input_size is not None
    )
    total_output_size = sum(
        result.output_size for result in results if result.output_size is not None
    )
    log(f"Total original size: {human_size(total_input_size)}")
    log(f"Total compressed size: {human_size(total_output_size)}")


def log_summary_table(rows: list[tuple[str, str, str, str]]) -> None:
    headers = ("filename", "status", "original size", "compressed size")
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    def emit_row(values: tuple[str, str, str, str]) -> None:
        line = "  ".join(
            value.ljust(widths[index]) for index, value in enumerate(values)
        )
        log(line)

    emit_row(headers)
    log("  ".join("-" * width for width in widths))
    for row in rows:
        emit_row(row)


def log(message: str) -> None:
    if LOGGER is None:
        print(message, flush=True)
        return
    LOGGER.log(message)


if __name__ == "__main__":
    raise SystemExit(main())
