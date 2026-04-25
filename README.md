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
  "crf": 22,
  "preset": "slow",
  "audio_mode": "auto",
  "audio_bitrate": "128k",
  "skip_if_codec": ["hevc"],
  "supported_extensions": [".mp4", ".mov", ".avi", ".mkv", ".m4v"]
}
```

Important options:

- `input_dir`: folder containing source videos.
- `output_dir`: folder for compressed or copied videos and run logs.
- `min_file_size_mb`: skip files smaller than this value when set.
- `min_duration_seconds`: skip files shorter than this value when set.
- `max_height`: downscale videos taller than this height.
- `crf`: x265 quality value. Lower means higher quality and larger files.
- `preset`: x265 encode speed/efficiency preset.
- `audio_mode`: `auto` caps high-bitrate audio, `copy` preserves source audio,
  and `aac` always re-encodes audio.
- `audio_bitrate`: AAC audio bitrate cap when `audio_mode` is `auto`, or target
  bitrate when `audio_mode` is `aac`.
- `skip_if_codec`: codecs to skip unless size rules make them eligible.
- `supported_extensions`: file extensions scanned in the input folder.

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
