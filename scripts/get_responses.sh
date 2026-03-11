#!/usr/bin/env bash
# Utility for running various commands against the HDMI Matrix and getting their
# responses so we can analyze

set -euo pipefail

# disallow running as root
if [[ "$EUID" -eq 0 ]]; then
  echo "This script should not be run as root. Please run it as a regular user with sudo privileges."
  exit 1
fi

: "${project_dir:="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"}"

PYTHON="$project_dir/.venv/bin/python3"

: "${out_dir:=$project_dir/test_fixtures/responses}"

commands=(
  spobsi01              # set output B to input 1
  spoa2plr14            # two-picture left/right mode
  spoa2pud14            # two-picture up/down mode
  spoa2x21              # 2x2 mode, combination 1
  spoa1b3s1             # 1 big, 3 small mode, combination 1
  spoapip14             # picture-in-picture mode, main picture input 1, pip input 4
  'spob copy outa on'   # copy output A to output B
  'spob copy outa off'  # stop copying output A to output B
  spoasi04              # set output A to input 4
)

cd "$project_dir"
if [[ -d "$out_dir" ]]; then
  if [[ "$*" == *"-f"* ]]; then
    echo "Output directory $out_dir already exists. Removing it since -f was passed."
    rm -rf "$out_dir"
  else
    echo "Output directory $out_dir already exists. Please remove it before running this script or invoke this script using -f."
    printf "   %q -f\n" "$0"
    echo " -- or -- "
    printf "   rm -rf %q\n" "$out_dir"
    exit 1
  fi
fi
mkdir -p "$out_dir"

i=0
for cmd in "${commands[@]}"; do
  echo "Command: $cmd"

  printf -v prefix "%02d_%s" "$i" "$cmd"
  : $(( i++ ))

  # Run the command and save its response
  response_file="$out_dir/${prefix}_response.bin"
  sudo "$PYTHON" main.py --bin "$cmd" > "$response_file"

  # Sleep between command invocations to avoid overwhelming the serial device.
  # Otherwise, the responses start getting mixed up between different files.
  sleep 1

  # Capture the status command output so we can determine how to parse each
  # state.
  sudo "$PYTHON" main.py --bin sta > "$out_dir/${prefix}_status.bin"

  sleep 1
done
