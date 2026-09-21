# ------------------------------------------------------------------------------------------
#  Copyright (c) Marco Caturano. All rights reserved.
#  Licensed under the GPLv3 License. See LICENSE in the project root for license information.
# ------------------------------------------------------------------------------------------
"""Offline check of blender_manifest.toml against the Extensions Platform rules.

``blender --command extension validate`` is the authority, but it needs a
Blender binary. This script mirrors the documented rules so the manifest can be
checked from CI or from a machine without Blender.

Usage:
    python tools/validate_manifest.py [path/to/blender_manifest.toml]
"""

import re
import sys
import tomllib
from pathlib import Path

REQUIRED_KEYS = (
    "schema_version",
    "id",
    "version",
    "name",
    "tagline",
    "maintainer",
    "type",
    "blender_version_min",
    "license",
)

OPTIONAL_KEYS = (
    "blender_version_max",
    "website",
    "copyright",
    "tags",
    "platforms",
    "wheels",
    "permissions",
    "build",
)

VALID_TYPES = ("add-on", "theme")
VALID_PERMISSIONS = ("files", "network", "clipboard", "camera", "microphone")
VALID_PLATFORMS = (
    "windows-x64",
    "windows-arm64",
    "macos-x64",
    "macos-arm64",
    "linux-x64",
)

# Tags accepted for add-ons by the Extensions Platform.
VALID_ADDON_TAGS = (
    "3D View",
    "Add Curve",
    "Add Mesh",
    "Animation",
    "Bake",
    "Camera",
    "Compositing",
    "Development",
    "Game Engine",
    "Geometry Nodes",
    "Grease Pencil",
    "Import-Export",
    "Lighting",
    "Material",
    "Modeling",
    "Mesh",
    "Node",
    "Object",
    "Paint",
    "Pipeline",
    "Physics",
    "Render",
    "Rigging",
    "Scene",
    "Sculpt",
    "Sequencer",
    "System",
    "Text Editor",
    "Tracking",
    "User Interface",
    "UV",
)

SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
VERSION_RANGE_RE = re.compile(r"^\d+\.\d+\.\d+$")
COPYRIGHT_RE = re.compile(r"^\d{4}(-\d{4})? +\S.*$")
PLACEHOLDER_RE = re.compile(r"REPLACE_WITH|TODO|XXX|example\.com", re.IGNORECASE)

MAX_TAGLINE = 64
MAX_PERMISSION_REASON = 64
END_PUNCTUATION = ".,;:!?"


def check(manifest_path):
    problems = []
    warnings = []

    try:
        data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        return ["Cannot read the manifest: %s" % error], []

    for key in REQUIRED_KEYS:
        if key not in data:
            problems.append("Missing required key: %s" % key)

    known = set(REQUIRED_KEYS) | set(OPTIONAL_KEYS)
    for key in data:
        if key not in known:
            problems.append("Unknown key: %s" % key)

    def empty(value):
        return value is None or value == "" or value == [] or value == {}

    for key, value in data.items():
        if empty(value):
            problems.append("Key %r is empty; omit it instead" % key)

    if data.get("schema_version") != "1.0.0":
        problems.append('schema_version must be "1.0.0"')

    extension_id = data.get("id", "")
    if extension_id and not ID_RE.match(extension_id):
        problems.append(
            "id must be lowercase letters, digits and underscores: %r" % extension_id
        )

    version = data.get("version", "")
    if version and not SEMVER_RE.match(version):
        problems.append("version must follow semantic versioning: %r" % version)

    tagline = data.get("tagline", "")
    if tagline:
        if len(tagline) > MAX_TAGLINE:
            problems.append(
                "tagline is %d characters, the limit is %d"
                % (len(tagline), MAX_TAGLINE)
            )
        if tagline[-1] in END_PUNCTUATION:
            problems.append("tagline cannot end with punctuation")
        if tagline[0].islower():
            warnings.append("tagline usually starts with a capital letter")

    if data.get("type") not in VALID_TYPES:
        problems.append("type must be one of %s" % (VALID_TYPES,))

    for key in ("blender_version_min", "blender_version_max"):
        value = data.get(key)
        if value and not VERSION_RANGE_RE.match(value):
            problems.append("%s must look like X.Y.Z: %r" % (key, value))
    minimum = data.get("blender_version_min", "")
    if minimum and tuple(int(p) for p in minimum.split(".")) < (4, 2, 0):
        problems.append("blender_version_min must be at least 4.2.0")

    licenses = data.get("license", [])
    if isinstance(licenses, list):
        for entry in licenses:
            if not isinstance(entry, str) or not entry.startswith("SPDX:"):
                problems.append("license entries must start with SPDX: %r" % entry)
    else:
        problems.append("license must be a list")

    for entry in data.get("copyright", []):
        if not COPYRIGHT_RE.match(entry):
            problems.append(
                'copyright must be "Year Name" or "Year-Year Name": %r' % entry
            )

    for tag in data.get("tags", []):
        if tag not in VALID_ADDON_TAGS:
            problems.append("Unknown tag %r" % tag)

    for platform in data.get("platforms", []):
        if platform not in VALID_PLATFORMS:
            problems.append("Unknown platform %r" % platform)

    for name, reason in (data.get("permissions") or {}).items():
        if name not in VALID_PERMISSIONS:
            problems.append("Unknown permission %r" % name)
        if not isinstance(reason, str) or not reason:
            problems.append("Permission %r needs an explanation" % name)
            continue
        if len(reason) > MAX_PERMISSION_REASON:
            problems.append(
                "Permission %r explanation is %d characters, the limit is %d"
                % (name, len(reason), MAX_PERMISSION_REASON)
            )
        if reason[-1] in END_PUNCTUATION:
            problems.append(
                "Permission %r explanation cannot end with punctuation" % name
            )

    build = data.get("build") or {}
    if "paths" in build and "paths_exclude_pattern" in build:
        problems.append(
            "build.paths and build.paths_exclude_pattern are mutually exclusive"
        )
    if "generated" in build:
        problems.append("build.generated is reserved for internal use")

    for key in ("maintainer", "website"):
        value = data.get(key, "")
        if isinstance(value, str) and PLACEHOLDER_RE.search(value):
            problems.append("%s still contains a placeholder: %r" % (key, value))

    package_dir = manifest_path.parent
    if data.get("type") == "add-on" and not (package_dir / "__init__.py").is_file():
        problems.append("An add-on needs an __init__.py next to the manifest")
    if not (package_dir / "LICENSE").is_file():
        warnings.append("No LICENSE file next to the manifest")

    return problems, warnings


def main(argv):
    if len(argv) > 1:
        manifest_path = Path(argv[1])
    else:
        here = Path(__file__).resolve().parent.parent
        manifest_path = here / "photometric_light_plus" / "blender_manifest.toml"

    problems, warnings = check(manifest_path)

    for warning in warnings:
        print("WARNING: %s" % warning)
    for problem in problems:
        print("ERROR:   %s" % problem)

    if problems:
        print("\n%d problem(s) found in %s" % (len(problems), manifest_path))
        return 1

    print("Manifest OK: %s" % manifest_path)
    print("Run 'blender --command extension validate' for the authoritative check.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
