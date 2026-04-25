# Python macOS Video Compression Tool Plan

## Summary

Build a small two-file tool:

- `compress_videos.py`: main Python script using only the standard library plus
  system `ffmpeg` and `ffprobe`.
- `config.json`: user-editable settings for folders, thresholds, codec choices,
  quality, scaling, audio handling, skip heuristics, and supported extensions.

The script scans `./input`, produces one output file per supported input in
`./output`, keeps filenames unchanged, never modifies `./input`, overwrites
existing output files on rerun, and only keeps compressed output when it is
smaller than the original.

## Architecture and Flow

`compress_videos.py` structure:

```text
main()
  load_config()
  validate_config()
  start_logging()
  ensure_tools_available()
  ensure_directories()
  discover_video_files()
  for each input file:
    probe_video()
    should_compress()
    copy_original() or compress_video()
    finalize_output()
  log_summary()
```

Core functions:

- `load_config(path="config.json") -> dict`
  - Read JSON config.
  - Merge user config with defaults.
  - Fail clearly if JSON is malformed.

- `validate_config(config) -> dict`
  - Validate types and ranges.
  - Normalize paths with `pathlib.Path`.
  - Normalize extensions to lowercase with leading dots.
  - Allow `min_file_size_mb` and `min_duration_seconds` to be `null` for "no limit".

- `ensure_tools_available()`
  - Use `shutil.which("ffmpeg")` and `shutil.which("ffprobe")`.
  - Exit with a clear message if either is missing.

- `ensure_directories(config)`
  - Require `input_dir` to exist.
  - Create `output_dir` when missing.
  - Reject matching input and output directories during config validation.

- `discover_video_files(input_dir, supported_extensions) -> list[Path]`
  - Non-recursive scan of `input_dir`.
  - Include only regular files with supported extensions.
  - Sort by filename for predictable output.
  - Create no changes inside `input`.

- `probe_video(path) -> dict`
  - Run `ffprobe` with JSON output.
  - Extract:
    - video codec, e.g. `h264`, `hevc`
    - width
    - height
    - duration seconds
    - bit rate if available
  - Use first video stream as the source of video metadata.
  - Prefer format duration/bitrate as fallback when stream fields are missing.

- `should_compress(path, metadata, config) -> tuple[bool, str]`
  - Return decision and reason.
  - Skip compression when:
    - file size is below `min_file_size_mb`
    - duration is below `min_duration_seconds`
    - resolution and bitrate indicate the file is already small enough
  - Practical codec rule:
    - Codec names can be used as an extra size guard, but not as a blanket bypass.

- `build_ffmpeg_command(input_path, output_path, metadata, config) -> list[str]`
  - Use argument list, never shell string, so filenames with spaces are safe.
  - Include `-y` to overwrite output.
  - Include `-map 0:v:0 -map 0:a?` to keep primary video and optional audio.
  - Encode video with `libx265`, configured CRF and preset.
  - Treat `audio_bitrate` as an audio ceiling: re-encode to AAC only when the
    source audio bitrate is above the ceiling, otherwise copy the original audio.
  - Add `-tag:v hvc1` for Apple compatibility.
  - Add scaling filter only when input height exceeds `max_height`.

- `compress_video(input_path, temp_output_path, metadata, config)`
  - Write to a temporary output path in `output`, not directly to the final filename.
  - If `ffmpeg` fails or produces no usable file, raise an encoding error so the
    caller can delete the temp file and copy the original to final output.

- `copy_original(input_path, final_output_path)`
  - Use `shutil.copy2`.
  - Overwrite existing output file.

- `finalize_output(input_path, temp_output_path, final_output_path)`
  - If compressed temp output is smaller than input, move temp to final filename.
  - If compressed output is same size or larger, delete temp and copy original to
    final filename.
  - Always leave exactly one final output file for every supported input file
    unless both processing and fallback copy fail.

## Config Loading and Example `config.json`

Defaults are embedded in `compress_videos.py` so missing optional fields still
work.

Recommended example:

```json
{
  "input_dir": "./input",
  "output_dir": "./output",
  "min_file_size_mb": 15,
  "min_duration_seconds": 10,
  "max_height": 1080,
  "crf": 23,
  "preset": "slow",
  "audio_mode": "aac",
  "audio_bitrate": "128k",
  "skip_if_codec": [],
  "supported_extensions": [".mp4", ".mov", ".avi", ".mkv", ".m4v"]
}
```

Validation rules:

- `input_dir` and `output_dir`: strings.
- `min_file_size_mb`: positive number, default `15`.
- `min_duration_seconds`: positive number, default `10`.
- `max_height`: positive integer, default `1080`.
- `crf`: integer, practical range `0..51`; default `23`.
- `preset`: string accepted by ffmpeg/libx265, default `slow`.
- `audio_mode`: `aac`, `copy`, or `auto`; default `aac`.
- `audio_bitrate`: string like `128k`, used as the AAC ceiling/target bitrate.
- `skip_if_codec`: optional list of lowercase codec names, only applied when the
  file is already small enough.
- `supported_extensions`: non-empty list of extensions.

## Skip Heuristics

The compressor avoids wasting time on files that are already small or low-bitrate:

- files shorter than `min_duration_seconds`
- files smaller than `min_file_size_mb`
- 360p or 480p clips that are already small
- 480p clips with very low bitrate
- 720p clips with low bitrate and modest file size
- 1080p clips with reasonably low bitrate and modest file size

These are the main guards that keep the encoder focused on files that are likely
to shrink.

## ffprobe and ffmpeg Details

`ffprobe` command shape:

```text
ffprobe -v error
  -print_format json
  -show_format
  -show_streams
  <input_file>
```

Metadata extraction:

- Find first stream where `codec_type == "video"`.
- `codec_name` from video stream.
- `width` and `height` from video stream.
- `duration` from video stream, else format duration.
- `bit_rate` from video stream, else format bit rate.
- If no video stream exists, treat file as invalid and copy original to output
  with an error message.

Scaling logic:

- If `height > max_height`, add:

  ```text
  -vf scale=-2:<max_height>
  ```

- If `height <= max_height`, omit `-vf`.
- `-2` preserves aspect ratio and ensures encoder-compatible even width.
- Never upscale.

`ffmpeg` command shape:

```text
ffmpeg -y
  -i <input_file>
  -map 0:v:0
  -map 0:a?
  -c:v libx265
  -crf <crf>
  -preset <preset>
  -tag:v hvc1
  [-vf scale=-2:<max_height>]
  -c:a aac
  -b:a <audio_bitrate>
  <temp_output_file>
```

Temporary output naming:

- Final output path: `output / input.name`
- Temp output path: `output / (input.stem + ".tmp" + input.suffix)`
- Existing final and temp paths are overwritten or removed before use.

## Decision Logic

For each supported input:

1. Probe metadata.
2. If probe fails:
   - Log the failure.
   - Copy original to output.
3. If file is below configured minimum size:
   - Copy original to output.
4. If duration is below configured minimum duration:
   - Copy original to output.
5. If resolution and bitrate indicate the file is already small enough:
   - Copy original to output.
6. Otherwise compress.
7. Compare compressed temp file size against input size.
8. Keep compressed output only if it is smaller.
9. Otherwise copy original to output.

This is intentionally heuristic-based, not a perfect video-quality classifier.

## Logging and Error Handling

Console output should be clear and compact, and the same timestamped output is
written to a run log under `output/logs/`.

Example:

```text
2026-04-25T12:00:00 [LOG] Writing run log to output/logs/compress_videos_20260425_120000.log
2026-04-25T12:00:00 [SCAN] Found 3 supported files
2026-04-25T12:00:01 [PROBE] input/My Video.mov: h264, 3840x2160, 125.4s, 820.0 MB
2026-04-25T12:00:01 [COMPRESS] My Video.mov: libx265 crf=23 preset=slow, audio=aac 128k, scaling to 1080p
2026-04-25T12:03:20 [KEEP] My Video.mov: 820.0 MB -> 214.0 MB
2026-04-25T12:03:20 [SKIP] Small Clip.mp4: below min file size; copied original
2026-04-25T12:03:21 [FALLBACK] Archive.mkv: compressed file was larger; copied original
2026-04-25T12:03:21 [ERROR] Broken.mov: ffprobe failed; copied original
2026-04-25T12:03:21 [DONE] compressed=1 skipped=1 retained=1 failed=1
```

Error strategy:

- Missing `input_dir`: print clear error and exit non-zero.
- Missing `output_dir`: create it.
- Missing `ffmpeg` or `ffprobe`: print install guidance and exit non-zero.
- Per-file probe or encode failure: log error, copy original to output, continue
  processing remaining files.
- Copy failure: log error and count as failed because the "one output per input"
  guarantee could not be met.
- Keyboard interrupt: stop cleanly, leave already completed outputs intact, and
  summarize completed files.

## Edge Cases

Handle explicitly:

- Filenames with spaces, quotes, parentheses, and Unicode via
  `subprocess.run([...])`, never `shell=True`.
- Existing output file from previous run gets overwritten.
- Existing temp file gets removed before reuse.
- Input with no audio works because audio map is optional.
- Input with multiple audio tracks: v1 keeps mapped optional audio according to
  ffmpeg behavior; no complex stream preservation.
- Corrupt file or missing video stream: copy original to output if possible.
- Output directory same as input directory: reject during config validation to
  avoid modifying input files.
- Unsupported extensions are ignored.
- Case-insensitive extensions are supported.
- Compression larger than source always falls back to original.
- HEVC files are no longer skipped by default. Codec is one signal among file
  size, duration, resolution, and bitrate.
- Audio is copied when the source bitrate is at or below the ceiling so low
  bitrate clips do not get re-encoded upward.

## Tradeoffs and Assumptions

- The scan is non-recursive for simplicity.
- The tool targets local batch use on macOS, not a daemon or GUI.
- Output filenames remain exactly the same, including original extension, even
  though the encoded video becomes HEVC.
- The script will not preserve all metadata, subtitles, chapters, or every stream
  in v1.
- Heuristics are intentionally practical instead of optimal.
- No third-party Python packages are used.
- No automatic installation of `ffmpeg` or `ffprobe`; the user must install them
  separately.
- Manual verification with small sample files is preferred before running against
  a large video folder.

## Clean Python Script Structure

Suggested function list:

```python
DEFAULT_CONFIG = {...}

def main() -> int: ...

def load_config(config_path: Path) -> dict: ...
def merge_config(user_config: dict) -> dict: ...
def validate_config(config: dict) -> dict: ...

def ensure_tools_available() -> None: ...
def ensure_directories(config: dict) -> None: ...

def discover_video_files(input_dir: Path, extensions: set[str]) -> list[Path]: ...

def probe_video(path: Path) -> dict: ...
def parse_ffprobe_metadata(data: dict) -> dict: ...

def should_compress(path: Path, metadata: dict, config: dict) -> tuple[bool, str]: ...

def build_ffmpeg_command(
    input_path: Path,
    output_path: Path,
    metadata: dict,
    config: dict,
) -> list[str]: ...

def compress_video(
    input_path: Path,
    temp_output_path: Path,
    metadata: dict,
    config: dict,
) -> None: ...

def copy_original(input_path: Path, final_output_path: Path) -> None: ...

def finalize_output(
    input_path: Path,
    temp_output_path: Path,
    final_output_path: Path,
) -> ProcessResult: ...

def log_summary(results: list[ProcessResult]) -> None: ...
def human_size(num_bytes: int) -> str: ...
def log(message: str) -> None: ...
```

`main()` should return an exit code and the script should end with:

```python
if __name__ == "__main__":
    raise SystemExit(main())
```

## Test and Acceptance Plan

Manual acceptance scenarios:

- A large H.264 `4K` file compresses to HEVC and scales down to 1080p.
- A `720p` or `1080p` file compresses without scaling.
- A tiny file is copied when `min_file_size_mb` is configured.
- A very short file is copied when `min_duration_seconds` is configured.
- An HEVC file is copied only when the heuristics say it is already small enough.
- A compressed output larger than the input is deleted and replaced with the
  original.
- A filename with spaces is processed correctly.
- A corrupt video logs an error and copies original if possible.
- Rerunning overwrites files in `output`.
- `input` files remain unchanged.

Optional command checks after implementation:

```text
python3 compress_videos.py
ffprobe output/<filename>
```
