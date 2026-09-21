# ------------------------------------------------------------------------------------------
#  Copyright (c) Marco Caturano. All rights reserved.
#  Licensed under the GPLv3 License. See LICENSE in the project root for license information.
# ------------------------------------------------------------------------------------------
"""Photometric Light Plus - IES lights with a GPU halo preview.

Packaged as a Blender extension (manifest driven), compatible with Blender 4.5
and 5.x. No ``bl_info`` on purpose: extensions take their metadata from
``blender_manifest.toml``.
"""

import bpy
from bpy.app.handlers import persistent

from . import energy, operators, overlay, properties, storage, ui

__all__ = ("register", "unregister")

_MODULES = (
    properties,
    operators,
    ui,
)


@persistent
def _on_load_pre(_dummy):
    """GPU batches belong to the previous file, drop them before it is freed."""
    overlay.clear_batch_cache()
    storage.clear_parse_cache()


@persistent
def _on_load_post(_dummy):
    """Make sure every IES light in the opened file is self contained."""
    overlay.clear_batch_cache()
    storage.clear_parse_cache()

    for light_data in list(bpy.data.lights):
        props = getattr(light_data, "phlp", None)
        if props is None:
            continue
        if props.has_ies and props.ies_text is not None:
            continue
        if storage.find_ies_node(light_data) is None:
            continue
        # Lights created by the 1.x prototype, or files whose IES node still
        # points at an external path, are migrated here.
        if storage.ensure_internal_ies(light_data):
            energy.refresh_photometry_info(light_data)


@persistent
def _on_undo_redo(_dummy):
    """Data-block identities can change across undo steps, invalidate caches."""
    overlay.clear_batch_cache()
    storage.clear_parse_cache()


_HANDLERS = (
    (bpy.app.handlers.load_pre, _on_load_pre),
    (bpy.app.handlers.load_post, _on_load_post),
    (bpy.app.handlers.undo_post, _on_undo_redo),
    (bpy.app.handlers.redo_post, _on_undo_redo),
)


def register():
    for module in _MODULES:
        module.register()

    for handler_list, callback in _HANDLERS:
        if callback not in handler_list:
            handler_list.append(callback)

    bpy.types.VIEW3D_MT_light_add.append(operators.menu_func)
    overlay.register_handler()


def unregister():
    overlay.unregister_handler()

    try:
        bpy.types.VIEW3D_MT_light_add.remove(operators.menu_func)
    except (ValueError, AttributeError):
        pass

    for handler_list, callback in _HANDLERS:
        if callback in handler_list:
            handler_list.remove(callback)

    for module in reversed(_MODULES):
        module.unregister()

    storage.clear_parse_cache()
