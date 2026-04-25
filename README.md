# compress_videos

Batch-compress videos from an input folder into an output folder using `ffmpeg`.

The script probes each supported video, decides whether it should be compressed,
then writes the final file to the configured output directory. If compression
does not reduce the file size, the original file is copied instead.

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
  "skip_existing_outputs": true,
  "enable_smart_skip": true,
  "enable_sample_preflight": false,
  "sample_preflight_seconds": 8,
  "sample_preflight_min_ratio": 0.98,
  "parallel_jobs": 1,
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
- `skip_existing_outputs`: skip an input when a newer non-empty output file with
  the same name already exists.
- `enable_smart_skip`: skip files that are likely to waste encode time because
  they are already short, small, low-resolution, or low-bitrate.
- `enable_sample_preflight`: for HEVC inputs, encode a short sample first and
  skip the full encode if the sample does not predict useful savings.
- `sample_preflight_seconds`: number of seconds encoded when sample preflight is
  enabled.
- `sample_preflight_min_ratio`: sample preflight skips the full encode when the
  projected output is at least this fraction of the original size.
- `parallel_jobs`: number of videos to process at the same time. Keep this low
  because x265 already uses multiple CPU threads per file.
- `supported_extensions`: file extensions scanned in the input folder.

## Skip Heuristics

When `enable_smart_skip` is true, the compressor avoids wasting time on files
that are already small or low-bitrate:

- files shorter than `min_duration_seconds`, when configured
- files smaller than `min_file_size_mb`, when configured
- 360p or 480p clips that are already small
- 480p clips with very low bitrate
- 720p clips with low bitrate and modest file size
- 1080p clips with reasonably low bitrate and modest file size

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
