#!/usr/bin/env bash
# Builds libopus's decoder to opus.wasm, for the relay to turn a page's webm
# into PCM for voice isolation. Runs inside the pinned Emscripten image:
#   docker run --rm -v "$PWD":/src -w /src/intercom/opus emscripten/emsdk:4.0.23 bash build.sh
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
WORK="$HERE/.work"
OUT="${OUT:-$HERE/opus.wasm}"
mkdir -p "$WORK"

OPUS_VERSION=1.5.2
OPUS_URL="https://github.com/xiph/opus/releases/download/v$OPUS_VERSION/opus-$OPUS_VERSION.tar.gz"
OPUS_SHA=65c1d2f78b9f2fb20082c38cbe47c951ad5839345876e46941612ee87f9a7ce1

[[ -f "$WORK/opus.tar.gz" ]] || curl -fsSL "$OPUS_URL" -o "$WORK/opus.tar.gz"
echo "$OPUS_SHA  $WORK/opus.tar.gz" | sha256sum -c --quiet

SRC="$WORK/opus-$OPUS_VERSION"
[[ -d "$SRC" ]] || tar -xzf "$WORK/opus.tar.gz" -C "$WORK"
BUILD="$WORK/build"
[[ -f "$BUILD/Makefile" ]] || emcmake cmake -S "$SRC" -B "$BUILD" -DCMAKE_BUILD_TYPE=Release \
  -DOPUS_BUILD_PROGRAMS=OFF -DOPUS_BUILD_TESTING=OFF -DOPUS_INSTALL_PKG_CONFIG_MODULE=OFF \
  -DOPUS_INSTALL_CMAKE_CONFIG_MODULE=OFF -DOPUS_DISABLE_INTRINSICS=ON
cmake --build "$BUILD" -j"$(nproc)"

emcc -O3 "$BUILD/libopus.a" -sSTANDALONE_WASM --no-entry -sFILESYSTEM=0 \
  -sEXPORTED_FUNCTIONS=_opus_decoder_create,_opus_decode,_opus_decoder_destroy,_malloc,_free \
  -sINITIAL_MEMORY=1048576 -sSTACK_SIZE=65536 -o "$OUT"
ls -la "$OUT"
