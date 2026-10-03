#!/usr/bin/env bash
# Render .docx files to PNG for a visual check: preview.sh <out_dir> <file.docx>...
# Installs the bundled fonts first so LibreOffice lays text out the way Word will.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
out="$1"
shift
mkdir -p "$out" ~/.fonts
cp -n "$here"/../fonts/*.ttf ~/.fonts/ 2>/dev/null || true
fc-cache -f ~/.fonts >/dev/null

for docx in "$@"; do
  soffice --headless --convert-to pdf --outdir "$out" "$docx" >/dev/null
  pdf="$out/$(basename "${docx%.*}").pdf"
  pdftoppm -r 100 -png "$pdf" "${pdf%.pdf}"
  ls "${pdf%.pdf}"*.png
done
