#!/usr/bin/env bash
# Download and verify the pinned Open Ontologies v2.1.0 release for this platform.
# Run manually; the application itself never downloads or executes unverified binaries.
#   scripts/setup_open_ontologies.sh              download + verify into data/tools
#   scripts/setup_open_ontologies.sh --write-env  also set ONTO_OPEN_ONTOLOGIES_* in .env
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION=v2.1.0
BASE="https://github.com/fabio-rovai/open-ontologies/releases/download/$VERSION"

# Digests published in the release's SHASUMS.txt. The downloaded SHASUMS.txt must agree with these.
pinned() {
  case "$1" in
    open-ontologies-aarch64-apple-darwin) echo 493f6db51550112528d19f3ca88caf133b46c2a9054a195a98bc588b57ba07b6 ;;
    open-ontologies-x86_64-apple-darwin) echo d2aacac75b2301eb3ce8f49000a8cc0bc7dd7edbf01fa36ac422ffdf4aaf11ee ;;
    open-ontologies-x86_64-unknown-linux-gnu) echo ead03c7148a66ea0fe01cbdaa289d47361c2a4d54619a60f805a72a2c57e81bb ;;
    open-ontologies-x86_64-pc-windows-msvc.exe) echo 1d81ff3933868bfed001b63d8bdd6f9cb3c0807389d4e3064a51a41d7d60f480 ;;
    *) return 1 ;;
  esac
}

case "$(uname -s)/$(uname -m)" in
  Darwin/arm64) ASSET=open-ontologies-aarch64-apple-darwin ;;
  Darwin/x86_64) ASSET=open-ontologies-x86_64-apple-darwin ;;
  Linux/x86_64) ASSET=open-ontologies-x86_64-unknown-linux-gnu ;;
  MINGW*/x86_64|MSYS*/x86_64|CYGWIN*/x86_64) ASSET=open-ontologies-x86_64-pc-windows-msvc.exe ;;
  *) echo "No published $VERSION build for $(uname -s)/$(uname -m). Build from source and set ONTO_OPEN_ONTOLOGIES_* yourself." >&2; exit 1 ;;
esac

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

EXPECTED="$(pinned "$ASSET")"
TOOLS="$PWD/data/tools"; TARGET="$TOOLS/$ASSET"
mkdir -p "$TOOLS"
if [ -f "$TARGET" ] && [ "$(sha256 "$TARGET")" = "$EXPECTED" ]; then
  echo "Already installed and verified: $TARGET"
else
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  echo "Downloading $ASSET ($VERSION)…"
  curl -fsSL "$BASE/SHASUMS.txt" -o "$TMP/SHASUMS.txt"
  curl -fSL --progress-bar "$BASE/$ASSET" -o "$TMP/$ASSET"
  PUBLISHED="$(awk -v a="$ASSET" '$2==a {print $1}' "$TMP/SHASUMS.txt")"
  ACTUAL="$(sha256 "$TMP/$ASSET")"
  if [ "$PUBLISHED" != "$EXPECTED" ] || [ "$ACTUAL" != "$EXPECTED" ]; then
    echo "Checksum mismatch for $ASSET. expected=$EXPECTED published=${PUBLISHED:-missing} actual=$ACTUAL. Nothing installed." >&2
    exit 1
  fi
  mv "$TMP/$ASSET" "$TARGET"; chmod +x "$TARGET"
  echo "Installed and verified: $TARGET"
fi

if [ "${1:-}" = "--write-env" ]; then
  touch .env
  grep -v '^ONTO_OPEN_ONTOLOGIES_\(BIN\|SHA256\)=' .env > .env.tmp || true
  printf 'ONTO_OPEN_ONTOLOGIES_BIN=%s\nONTO_OPEN_ONTOLOGIES_SHA256=%s\n' "$TARGET" "$EXPECTED" >> .env.tmp
  mv .env.tmp .env
  echo "Updated .env. Restart the server to enable Open Ontologies."
else
  printf 'Add to .env (or re-run with --write-env):\nONTO_OPEN_ONTOLOGIES_BIN=%s\nONTO_OPEN_ONTOLOGIES_SHA256=%s\n' "$TARGET" "$EXPECTED"
fi
