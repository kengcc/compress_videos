# compress_videos

Batch-compress videos from an input folder into an output folder using `ffmpeg`.

The script probes each supported video, decides whether it should be compressed,
then writes the final file to the configured output directory. If compression
does not reduce the file size, the original file is copied instead.

## Scripts

The main entry point is [`compress_videos.py`](compress_videos.py), but this
repository also includes a few helper scripts for organizing media files:

- [`rename_input_videos.py`](rename_input_videos.py): rename files in `input/`
  based on their creation time or normalize filenames that begin with spaced
  dates like `yyyy mm dd...`.
- [`check filename dates.py`](check%20filename%20dates.py): scan year-named
  folders and report files whose filename date differs from metadata by more
  than 5 days.
- [`rename_postprocessing_filenames.py`](rename_postprocessing_filenames.py):
  rename media files in year-named folders to the `yyyymmdd_hhmmss_xxx`
  format after comparing filename dates with metadata timestamps.
- [`update_date.sh`](update_date.sh): shell helper for updating file dates.

## Requirements

- Python 3.10 or newer
- `ffmpeg`
- `ffprobe`

Both `ffmpeg` and `ffprobe` must be available on `PATH`.

On macOS with Homebrew:

```sh
brew install ffmpeg
```

## Setup

Create the input and output folders if they do not already exist:

```sh
mkdir -p input output
```

Place source videos in `input/`. The default supported extensions are:

```text
.mp4, .mov, .avi, .mkv, .m4v
```

## Usage

Run the compressor from the repository root:

```sh
python3 compress_videos.py
```

By default, compressed or copied videos are written to `output/` with the same
filename as the source video.

## Configuration

Settings are loaded from `config.json`. If the file is missing, the script uses
the built-in defaults.

Current configuration:

```json
{
  "input_dir": "./input",
  "output_dir": "./output",
  "min_file_size_mb": null,
  "min_duration_seconds": null,
  "max_height": 1080,
  "crf": 23,
  "preset": "medium",
  "audio_mode": "aac",
  "audio_bitrate": "128k",
  "skip_if_codec": [],
  "skip_codec_default_max_size_mb": 20,
  "skip_existing_outputs": true,
  "enable_smart_skip": true,
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
  "enable_sample_preflight": false,
  "sample_preflight_codecs": ["hevc"],
  "sample_preflight_min_duration_seconds": 5,
  "sample_preflight_seconds": 8,
  "sample_preflight_min_ratio": 0.98,
  "parallel_jobs": 1,
  "error_log_line_count": 8,
  "supported_extensions": [".mp4", ".mov", ".avi", ".mkv", ".m4v"]
}
```

Important options:

- `input_dir`: folder containing source videos.
- `output_dir`: folder for compressed or copied videos and run logs.
- `min_file_size_mb`: skip files smaller than this value before encoding, or
  `null` to disable this specific rule.
- `min_duration_seconds`: skip files shorter than this value before encoding, or
  `null` to disable this specific rule.
- `max_height`: downscale videos taller than this height.
- `crf`: x265 quality value. Lower means higher quality and larger files.
- `preset`: x265 encode speed/efficiency preset.
- `audio_mode`: `aac` caps audio at `audio_bitrate`, `copy` preserves source
  audio, and `auto` behaves like `aac` but keeps a conservative fallback for
  unknown source bitrate.
- `audio_bitrate`: AAC audio bitrate ceiling when `audio_mode` is `aac` or the
  cap used by `auto`.
- `skip_if_codec`: optional codec names to skip only when the file is already
  small enough.
- `skip_codec_default_max_size_mb`: size cutoff used with `skip_if_codec` when
  `min_file_size_mb` is `null`, or `null` to disable this codec-size rule.
- `skip_existing_outputs`: skip an input when a newer non-empty output file with
  the same name already exists.
- `enable_smart_skip`: skip files that are likely to waste encode time because
  they are already short, small, low-resolution, or low-bitrate.
- `smart_skip_short_duration_seconds`: skip clips shorter than this value, or
  `null` to disable this smart-skip rule.
- `smart_skip_low_resolution_height`: height cutoff for the low-resolution smart
  skip rule.
- `smart_skip_low_resolution_size_mb`: skip low-resolution clips smaller than
  this value, or `null` to disable this smart-skip rule.
- `smart_skip_480p_height` and `smart_skip_480p_max_bitrate`: skip files at or
  below this height when bitrate is below this value. Set the bitrate to `null`
  to disable this smart-skip rule.
- `smart_skip_720p_height`, `smart_skip_720p_max_size_mb`, and
  `smart_skip_720p_max_bitrate`: skip files at or below this height only when
  both size and bitrate are below these values. Set either size or bitrate to
  `null` to disable this smart-skip rule.
- `smart_skip_1080p_height`, `smart_skip_1080p_max_size_mb`, and
  `smart_skip_1080p_max_bitrate`: skip files at or below this height only when
  both size and bitrate are below these values. Set either size or bitrate to
  `null` to disable this smart-skip rule.
- `enable_sample_preflight`: for HEVC inputs, encode a short sample first and
  skip the full encode if the sample does not predict useful savings.
- `sample_preflight_codecs`: codec names that are eligible for sample preflight.
- `sample_preflight_min_duration_seconds`: minimum duration required before
  sample preflight runs, or `null` to allow any duration.
- `sample_preflight_seconds`: number of seconds encoded when sample preflight is
  enabled.
- `sample_preflight_min_ratio`: sample preflight skips the full encode when the
  projected output is at least this fraction of the original size.
- `parallel_jobs`: number of videos to process at the same time. Keep this low
  because x265 already uses multiple CPU threads per file.
- `error_log_line_count`: number of trailing ffmpeg stderr lines included when
  an encode or sample preflight fails.
- `supported_extensions`: file extensions scanned in the input folder.

## Skip Heuristics

When `enable_smart_skip` is true, the compressor avoids wasting time on files
that are already small or low-bitrate:

- files shorter than `min_duration_seconds`, when configured
- files smaller than `min_file_size_mb`, when configured
- clips shorter than `smart_skip_short_duration_seconds`
- low-resolution clips smaller than `smart_skip_low_resolution_size_mb`
- clips at or below `smart_skip_480p_height` and below
  `smart_skip_480p_max_bitrate`
- clips at or below `smart_skip_720p_height` and below both
  `smart_skip_720p_max_size_mb` and
  `smart_skip_720p_max_bitrate`
- clips at or below `smart_skip_1080p_height` and below both
  `smart_skip_1080p_max_size_mb` and
  `smart_skip_1080p_max_bitrate`

These are the main guards that keep the encoder focused on files that are likely
to shrink.

For reruns, `skip_existing_outputs` is usually the fastest safe option because
it avoids reprocessing files that already have an output. Set it to `false`
when you intentionally want to recompress existing outputs after changing
quality or skip settings.

## Outcomes

Each processed file is reported as one of:

- `converted`: compression succeeded and produced a smaller output.
- `skipped`: the file did not meet compression rules and the original was copied.
- `retained`: compression worked, but the compressed output was larger, so the
  original was copied.
- `failed`: probing, encoding, or fallback copying failed. The reason is logged.

## Logs

Each run prints timestamped messages to the terminal and writes the same output
to a timestamped log file under the configured output directory:

```text
output/logs/compress_videos_YYYYMMDD_HHMMSS.log
```

The end of each run includes counts and file lists for converted, skipped,
retained, and failed files.

## Git Hygiene

The `input/` and `output/` folders are ignored by git because they may contain
large local media files and generated outputs. Logs, Python cache files, local
virtual environments, and common editor or OS files are also ignored.
