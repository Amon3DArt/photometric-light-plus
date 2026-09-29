# ------------------------------------------------------------------------------------------
#  Copyright (c) Marco Caturano. All rights reserved.
#  Licensed under the GPLv3 License. See LICENSE in the project root for license information.
# ------------------------------------------------------------------------------------------
"""Self contained storage of the IES payload.

Everything this add-on needs at runtime lives inside the .blend file:

* the raw IES text is copied into a ``bpy.data.texts`` data-block,
* the ``ShaderNodeTexIES`` node is forced to ``mode = 'INTERNAL'`` and points at
  that text data-block,
* the halo overlay and the power computation read the text data-block only.

As a result the .blend keeps working after being moved to another machine, and
no temporary file is ever written to disk.
"""

import os

import bpy

from . import ies_parser, ldt_parser

__all__ = (
    "SUPPORTED_EXTENSIONS",
    "TEXT_PREFIX",
    "clear_parse_cache",
    "ensure_internal_ies",
    "get_ies_text",
    "get_photometry",
    "pack_light_ies",
    "read_ies_file",
    "read_photometric_file",
    "store_ies_text",
)

TEXT_PREFIX = "IES_"

# EULUMDAT files are converted on import, so only LM-63 ever reaches the .blend.
LDT_EXTENSIONS = (".ldt", ".eul", ".ltd")
SUPPORTED_EXTENSIONS = (".ies",) + LDT_EXTENSIONS

# Parsed photometry cache. Keyed by ``(text.session_uid, len(text))`` plus the
# light side version counter, so a re-import always invalidates it.
# Only ever touched from the main thread (operators, UI, draw handler).
_PARSE_CACHE = {}
_PARSE_CACHE_LIMIT = 64


def clear_parse_cache():
    _PARSE_CACHE.clear()


def read_ies_file(filepath):
    """Read an IES file from disk once, at import time."""
    if not filepath or not os.path.isfile(filepath):
        raise ies_parser.IESParseError("File not found: %s" % filepath)
    size = os.path.getsize(filepath)
    if size > 64 * 1024 * 1024:
        raise ies_parser.IESParseError("File too large: %s" % filepath)
    with open(filepath, "r", encoding="latin-1", errors="ignore") as handle:
        return handle.read()


def read_photometric_file(filepath):
    """Read an IES or EULUMDAT file and always return an LM-63 payload.

    Returns ``(ies_content, was_converted)``. The conversion happens once, at
    import time, so the .blend only ever stores IES data.
    """
    raw = read_ies_file(filepath)
    extension = os.path.splitext(filepath)[1].lower()

    is_ldt = extension in LDT_EXTENSIONS
    if not is_ldt and extension != ".ies":
        # Unknown extension: fall back to sniffing the content.
        is_ldt = ldt_parser.looks_like_ldt(raw)
    elif not is_ldt and ldt_parser.looks_like_ldt(raw):
        # Mislabelled EULUMDAT shipped with an .ies extension.
        is_ldt = True

    if not is_ldt:
        return raw, False

    source_name = os.path.basename(filepath)
    try:
        return ldt_parser.ldt_string_to_ies(raw, source_name=source_name), True
    except ldt_parser.LDTParseError as error:
        if extension == ".ies":
            # Sniffing was wrong, keep the original payload.
            return raw, False
        raise ies_parser.IESParseError("EULUMDAT conversion failed: %s" % error)


def store_ies_text(content, base_name):
    """Create or update the internal text data-block holding the IES payload."""
    name = TEXT_PREFIX + base_name
    text = bpy.data.texts.get(name)
    if text is None:
        text = bpy.data.texts.new(name)
    else:
        text.clear()
    text.write(content)
    # Keep the payload alive even when no node references it yet.
    text.use_fake_user = True
    return text


def get_ies_text(light_data):
    """Return the internal text data-block associated with a light, or None."""
    if light_data is None:
        return None
    props = getattr(light_data, "phlp", None)
    if props is None:
        return None
    text = props.ies_text
    if text is not None:
        return text

    # Fall back to whatever the node tree references, useful for lights that
    # were created by the 1.x prototype.
    node = find_ies_node(light_data)
    if node is not None and getattr(node, "ies", None) is not None:
        return node.ies
    return None


def find_ies_node(light_data):
    """Locate the IES texture node of a light, if the light has one.

    Reading ``node_tree`` is enough on every supported version: 4.5 returns None
    until nodes are enabled, 5.1+ always returns the tree.
    """
    tree = getattr(light_data, "node_tree", None)
    if tree is None:
        return None
    for node in tree.nodes:
        if node.bl_idname == "ShaderNodeTexIES":
            return node
    return None


def ensure_internal_ies(light_data):
    """Force a light to reference its IES data internally.

    Returns ``True`` when the light ends up fully self contained.
    """
    node = find_ies_node(light_data)
    if node is None:
        return False

    props = getattr(light_data, "phlp", None)
    text = props.ies_text if props is not None else None

    if text is None and getattr(node, "ies", None) is not None:
        text = node.ies

    if text is None:
        filepath = bpy.path.abspath(getattr(node, "filepath", "") or "")
        if not filepath or not os.path.isfile(filepath):
            return False
        try:
            content = read_ies_file(filepath)
        except ies_parser.IESParseError:
            return False
        base = os.path.splitext(os.path.basename(filepath))[0]
        text = store_ies_text(content, base)
        if props is not None:
            props.source_name = os.path.basename(filepath)

    node.mode = "INTERNAL"
    node.ies = text
    if hasattr(node, "filepath"):
        node.filepath = ""
    text.use_fake_user = True

    if props is not None:
        props.ies_text = text
        props.has_ies = True
        props.web_version += 1
    return True


def needs_packing(light_data):
    """True when the light still depends on a file outside the .blend.

    Read only on purpose: the add-on reports the situation and lets the user
    decide, it never rewrites a file it did not create.
    """
    node = find_ies_node(light_data)
    if node is None:
        return False
    if getattr(node, "mode", "") == "EXTERNAL":
        return True
    return getattr(node, "ies", None) is None


def pack_light_ies(light_data):
    """Public helper used by the operators, mirrors ``ensure_internal_ies``."""
    return ensure_internal_ies(light_data)


def get_photometry(light_data):
    """Return the parsed photometry for a light, or ``None``.

    Never raises: the draw handler depends on it and must stay silent.
    """
    text = get_ies_text(light_data)
    if text is None:
        return None

    props = getattr(light_data, "phlp", None)
    version = props.web_version if props is not None else 0
    key = (text.session_uid, version)

    cached = _PARSE_CACHE.get(key)
    if cached is not None:
        return cached

    try:
        photometry = ies_parser.parse_ies_string(text.as_string())
    except Exception as error:  # noqa: BLE001 - defensive, UI must not break
        print("[Photometric Light Plus] IES parse failed for %r: %s"
              % (text.name, error))
        _PARSE_CACHE[key] = None
        return None

    if len(_PARSE_CACHE) >= _PARSE_CACHE_LIMIT:
        _PARSE_CACHE.clear()
    _PARSE_CACHE[key] = photometry
    return photometry
