#!/usr/bin/env bash
set -euo pipefail

# Standalone Tailwind CLI binary (no Node required).
# https://tailwindcss.com/blog/standalone-cli
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="$ROOT/tools/bin"
mkdir -p "$BIN_DIR"

# Detect platform
PLATFORM=""
case "$(uname -s)" in
  Linux*)
    case "$(uname -m)" in
      x86_64) PLATFORM="linux-x64";;
      aarch64|arm64) PLATFORM="linux-arm64";;
      *) echo "Unsupported Linux arch: $(uname -m)"; exit 1;;
    esac;;
  Darwin*)
    case "$(uname -m)" in
      x86_64) PLATFORM="macos-x64";;
      arm64) PLATFORM="macos-arm64";;
      *) echo "Unsupported macOS arch: $(uname -m)"; exit 1;;
    esac;;
  MINGW*|MSYS*|CYGWIN*) PLATFORM="windows-x64.exe";;
  *) echo "Unsupported platform: $(uname -s)"; exit 1;;
esac

VERSION="v3.4.13"
BIN="$BIN_DIR/tailwindcss-$PLATFORM"
URL="https://github.com/tailwindlabs/tailwindcss/releases/download/$VERSION/tailwindcss-$PLATFORM"

if [ ! -x "$BIN" ]; then
  echo "Downloading Tailwind CLI $VERSION for $PLATFORM..."
  curl -sSL "$URL" -o "$BIN"
  chmod +x "$BIN"
fi

INPUT="$ROOT/firefliesclearer/web/tailwind.input.css"
OUTPUT="$ROOT/firefliesclearer/web/static/styles.css"

echo "Compiling $INPUT → $OUTPUT..."
cd "$ROOT"
"$BIN" -i "$INPUT" -o "$OUTPUT" --minify
echo "Done."
