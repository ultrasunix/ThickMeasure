#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if command -v lit3prog >/dev/null 2>&1; then
  lit3prog -p < lit3_v2.0.bin
elif [[ -x "$SCRIPT_DIR/lit3prog" ]]; then
  "$SCRIPT_DIR/lit3prog" -p < lit3_v2.0.bin
else
  echo "lit3prog not found. Run bash setup.sh to install /usr/local/bin/lit3prog." >&2
  exit 127
fi

gpio mode 10  OUT 
gpio mode 11 OUT 
gpio mode 2 IN
gpio mode 3 IN
gpio mode 24 IN
gpio mode 6 OUT
gpio write 10 1
gpio write 11 1
gpio mode 12 alt0 
gpio mode 13 alt0 
gpio mode 14 alt0 
gpio mode 15 IN 
gpio mode 16 IN
gpio write 6 1

i2cdetect -y 1
