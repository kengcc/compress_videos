#!/bin/zsh

set -e

for f in *; do
  [[ -f "$f" ]] || continue

  # Extract date at start of filename
  date_part=$(echo "$f" | grep -oE '^[0-9]{4}-[0-9]{2}(-[0-9]{2})?')

  if [[ -z "$date_part" ]]; then
    echo "Skipping: $f"
    continue
  fi

  # If only YYYY-MM, add day = 01
  if [[ ${#date_part} -eq 7 ]]; then
    date_part="${date_part}-01"
  fi

  # Validate date
  if ! date -j -f "%Y-%m-%d" "$date_part" "+%Y-%m-%d" >/dev/null 2>&1; then
    echo "Invalid date: $f"
    continue
  fi

  iso_datetime="${date_part}T12:00:00"

  echo "Processing: $f -> $iso_datetime"

  base="${f%.*}"
  ext="${f##*.}"
  temp="${base}.__tmp__.${ext}"

  # Fix internal metadata (video)
  ffmpeg -y -i "$f" \
    -map 0 \
    -c copy \
    -map_metadata -1 \
    -metadata creation_time="$iso_datetime" \
    "$temp" >/dev/null 2>&1

  # Replace original
  mv "$temp" "$f"

  # Set filesystem dates
  touch -t "$(date -j -f "%Y-%m-%d %H:%M:%S" "$date_part 12:00:00" "+%Y%m%d1200")" "$f"
  SetFile -d "$(date -j -f "%Y-%m-%d %H:%M:%S" "$date_part 12:00:00" "+%m/%d/%Y %H:%M:%S")" "$f"

  echo "Done: $f"
done

