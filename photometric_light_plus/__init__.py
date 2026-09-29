# ------------------------------------------------------------------------------------------
#  Copyright (c) Marco Caturano. All rights reserved.
#  Licensed under the GPLv3 License. See LICENSE in the project root for license information.
# ------------------------------------------------------------------------------------------
"""Photometric Light Plus - IES lights with a GPU halo preview.

Packaged as a Blender extension (manifest driven), compatible with Blender 4.5
and 5.x. No ``bl_info`` on purpose: extensions take their metadata from
``blender_manifest.toml``.

Deliberately no application handlers
------------------------------------
This add-on registers nothing in ``bpy.app.handlers``. It never walks over the
data-blocks of a file it did not create, and it never modifies user data unless
the user asks for it through an operator. Opening a .blend costs exactly
nothing, whatever it contains.

The two caches the add-on keeps (parsed photometry and GPU batches) are keyed on
``ID.session_uid``, which Blender allocates from a counter that is unique for
the whole session and never reused. Entries belonging to a closed file can
therefore never be matched by mistake, so no load handler is needed to flush
them; both caches are size capped and evict on their own.

Lights whose IES payload still lives outside the .blend are reported in the
light data panel, with the operator that packs them one click away. Nothing is
migrated behind the user's back.
"""

import bpy

from . import operators, overlay, properties, storage, ui

__all__ = ("register", "unregister")

_MODULES = (
    properties,
    operators,
    ui,
)


def register():
    for module in _MODULES:
        module.register()

    bpy.types.VIEW3D_MT_light_add.append(operators.menu_func)
    overlay.register_handler()


def unregister():
    overlay.unregister_handler()

    try:
        bpy.types.VIEW3D_MT_light_add.remove(operators.menu_func)
    except (ValueError, AttributeError):
        pass

    for module in reversed(_MODULES):
        module.unregister()

    storage.clear_parse_cache()
