#!/usr/bin/env bash
# ------------------------------------------------------------------------------------------
#  Copyright (c) Marco Caturano. All rights reserved.
#  Licensed under the GPLv3 License. See LICENSE in the project root for license information.
# ------------------------------------------------------------------------------------------
#
# Builds and validates the extension package.
#
#   ./tools/build.sh                 use the "blender" found in PATH
#   BLENDER=/path/to/blender ./tools/build.sh
#
# The Extensions Platform expects blender_manifest.toml at the ROOT of the zip,
# so the build always runs from inside the package directory.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACKAGE_DIR="${ROOT_DIR}/photometric_light_plus"
DIST_DIR="${ROOT_DIR}/dist"
BLENDER="${BLENDER:-blender}"

echo "== Offline manifest check"
python3 "${ROOT_DIR}/tools/validate_manifest.py" "${PACKAGE_DIR}/blender_manifest.toml"

echo "== Byte-compiling the sources"
python3 -m compileall -q "${PACKAGE_DIR}"
find "${PACKAGE_DIR}" -name '__pycache__' -type d -prune -exec rm -rf {} +

mkdir -p "${DIST_DIR}"
rm -f "${DIST_DIR}"/*.zip

if command -v "${BLENDER}" >/dev/null 2>&1; then
  echo "== Building with ${BLENDER}"
  (cd "${PACKAGE_DIR}" && "${BLENDER}" --command extension build --output-dir "${DIST_DIR}")
  echo "== Validating the built package"
  "${BLENDER}" --command extension validate "${DIST_DIR}"/*.zip
else
  echo "== Blender not found, falling back to a plain zip"
  echo "   Install Blender 4.5+ and re-run to get the authoritative validation."
  VERSION="$(python3 - "${PACKAGE_DIR}/blender_manifest.toml" <<'PY'
import sys, tomllib
print(tomllib.load(open(sys.argv[1], "rb"))["version"])
PY
)"
  (cd "${PACKAGE_DIR}" && zip -r -q \
      "${DIST_DIR}/photometric_light_plus-${VERSION}.zip" . \
      -x '*__pycache__*' '*.pyc' '.DS_Store' '*/.git/*')
fi

echo
echo "== Package contents"
unzip -l "${DIST_DIR}"/*.zip
echo
echo "Done. Upload the zip from ${DIST_DIR} to https://extensions.blender.org"
