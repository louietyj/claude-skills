#!/usr/bin/env bash
# Builds WebRTC's AEC3 (freedesktop's webrtc-audio-processing) to aec3.wasm with
# wasm SIMD. Runs inside the pinned Emscripten image:
#   docker run --rm -v "$PWD":/src -w /src/intercom/aec emscripten/emsdk:4.0.23 bash build.sh
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
WORK="$HERE/.work"
OUT="${OUT:-$HERE/aec3.wasm}"
SIMD_FLAGS=(${SIMD_FLAGS--msimd128})
mkdir -p "$WORK"

WAP_VERSION=2.1
WAP_URL="https://freedesktop.org/software/pulseaudio/webrtc-audio-processing/webrtc-audio-processing-$WAP_VERSION.tar.xz"
WAP_SHA=ae9302824b2038d394f10213cab05312c564a038434269f11dbf68f511f9f9fe

fetch() {   # url sha256 dest
  [[ -f "$3" ]] || curl -fsSL "$1" -o "$3"
  echo "$2  $3" | sha256sum -c --quiet
}

# The image has pip but no venv module.
if [[ ! -x "$WORK/py/bin/meson" ]]; then
  python3 -m pip install --quiet --target "$WORK/py" "meson==1.12.1" "ninja==1.13.0"
fi
export PATH="$WORK/py/bin:$PATH" PYTHONPATH="$WORK/py"

SRC="$WORK/webrtc-audio-processing-$WAP_VERSION"
fetch "$WAP_URL" "$WAP_SHA" "$WORK/wap.tar.xz"
if [[ ! -d "$SRC" ]]; then
  tar -xf "$WORK/wap.tar.xz" -C "$WORK"
  # AEC3's SIMD paths are NEON or SSE2; Emscripten maps arm_neon.h onto wasm
  # SIMD, so meson takes NEON on wasm32. The ARM64 variants: the 32-bit sqrt
  # needs vrsqrteq_f32(0) == +inf, which SIMDe's isn't, and output went NaN.
  sed -i "s/^neon_opt = get_option('neon').require(have_neon)$/if host_machine.cpu_family() == 'wasm32'\n  have_neon = true\n  arch_cflags += ['-DWEBRTC_ARCH_ARM64']\nendif\n&/" "$SRC/meson.build"
  grep -q "cpu_family() == 'wasm32'" "$SRC/meson.build"
fi

TAG=$(echo "${SIMD_FLAGS[*]:-scalar}" | tr -c 'a-z0-9' '_')
BUILD="$WORK/build-$TAG"
CROSS="$WORK/cross-$TAG.ini"
args=$(printf "'%s', " -DWEBRTC_POSIX "${SIMD_FLAGS[@]}")
cat >"$CROSS" <<EOF
[binaries]
c = 'emcc'
cpp = 'em++'
ar = 'emar'
ranlib = 'emranlib'
strip = 'emstrip'

[built-in options]
c_args = [$args]
cpp_args = [$args]

[host_machine]
system = 'emscripten'
cpu_family = 'wasm32'
cpu = 'wasm32'
endian = 'little'
EOF

# abseil comes from the source's own meson wrap, which pins its hash.
NEON=enabled
[[ " ${SIMD_FLAGS[*]:-} " == *" -msimd128 "* ]] || NEON=disabled
[[ -f "$BUILD/build.ninja" ]] || meson setup "$BUILD" "$SRC" --cross-file "$CROSS" \
  --buildtype release -Db_ndebug=true --default-library static -Dneon=$NEON
LIBS=$(ninja -C "$BUILD" -t targets all | sed -n 's/^\([^:]*\.a\): .*/\1/p' | grep -v '\.p/' | sort)
ninja -C "$BUILD" $LIBS
LINK=("$BUILD/webrtc/modules/audio_processing/libwebrtc-audio-processing-2.a")
for lib in $LIBS; do [[ "$lib" == subprojects/abseil-cpp-*/libabsl_*.a ]] && LINK+=("$BUILD/$lib"); done
ABSEIL=$(ls -d "$SRC"/subprojects/abseil-cpp-*/ | head -1)

# wap.cc includes AEC3's own headers, so it takes the library's defines.
ARCH=()
[[ $NEON == enabled ]] && ARCH=(-DWEBRTC_HAS_NEON -DWEBRTC_ARCH_ARM64 -mfpu=neon)
em++ -std=c++17 -O3 -fno-exceptions "${SIMD_FLAGS[@]}" "${ARCH[@]}" -DWEBRTC_APM_DEBUG_DUMP=0 \
  -DWEBRTC_POSIX -DWEBRTC_LIBRARY_IMPL -DNDEBUG \
  -I "$SRC/webrtc" -I "$SRC" -I "$ABSEIL" -c "$HERE/wap.cc" -o "$BUILD/wap.o"
em++ -O3 "${SIMD_FLAGS[@]}" ${LINK_FLAGS:-} "$BUILD/wap.o" "${LINK[@]}" -sSTANDALONE_WASM --no-entry \
  -sALLOW_MEMORY_GROWTH=1 -sSTACK_SIZE=262144 -o "$OUT"
ls -la "$OUT"
