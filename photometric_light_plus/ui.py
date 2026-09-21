# ------------------------------------------------------------------------------------------
#  Copyright (c) Marco Caturano. All rights reserved.
#  Licensed under the GPLv3 License. See LICENSE in the project root for license information.
# ------------------------------------------------------------------------------------------
"""Panels for the light data properties and the 3D viewport sidebar."""

import bpy
from bpy.types import Panel

from . import storage

__all__ = ("classes", "register", "unregister")


def _engine_supports_ies(context):
    """EEVEE still ignores light node trees, so IES only renders in Cycles."""
    engine = getattr(context, "engine", "") or ""
    return not engine.startswith("BLENDER_EEVEE")


class PHLP_PT_photometry(Panel):
    bl_label = "Photometric Light"
    bl_idname = "PHLP_PT_photometry"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "data"
    bl_order = 10

    @classmethod
    def poll(cls, context):
        return getattr(context, "light", None) is not None

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        light_data = context.light
        props = light_data.phlp

        if not props.has_ies:
            column = layout.column(align=True)
            column.label(text="No IES payload on this light", icon="INFO")
            column.operator("phlp.replace_ies", icon="FILEBROWSER",
                            text="Load IES / LDT File")
            column.operator("phlp.pack_all_ies", icon="PACKAGE")
            return

        text = storage.get_ies_text(light_data)

        if not _engine_supports_ies(context):
            row = layout.row()
            row.alert = True
            row.label(
                text="EEVEE ignores IES, use Cycles to render it",
                icon="ERROR",
            )

        box = layout.box()
        box.label(text=props.info_line or "Photometry not evaluated yet",
                  icon="LIGHT_DATA")
        info = box.column(align=True)
        info.use_property_split = True
        row = info.row()
        row.label(text="Source")
        row.label(text=props.source_name or "-")
        row = info.row()
        row.label(text="Stored As")
        row.label(text=(text.name if text is not None else "MISSING"),
                  icon="TEXT" if text is not None else "ERROR")

        column = layout.column(align=True)
        column.prop(props, "flux_source")
        lumen_based = props.flux_source in {"INTEGRATED", "DECLARED"}
        sub = column.column(align=True)
        sub.enabled = lumen_based
        sub.prop(props, "efficacy_mode")
        efficacy = sub.column()
        efficacy.enabled = lumen_based and props.efficacy_mode == "CUSTOM"
        efficacy.prop(props, "custom_efficacy")
        column.prop(props, "intensity_multiplier")

        column = layout.column(align=True)
        column.enabled = False
        column.prop(props, "computed_lumens", text="Flux (lm)")
        column.prop(props, "declared_lumens", text="Declared (lm)")
        column.prop(props, "max_candela", text="Max (cd)")
        column.prop(props, "beam_angle", text="Beam (deg)")

        row = layout.row(align=True)
        row.operator("phlp.reload_photometry", icon="FILE_REFRESH")
        row.operator("phlp.replace_ies", icon="FILEBROWSER", text="Replace")
        layout.operator("phlp.validate_ies", icon="CONSOLE")


class PHLP_PT_halo(Panel):
    bl_label = "Photometric Halo"
    bl_idname = "PHLP_PT_halo"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "data"
    bl_parent_id = "PHLP_PT_photometry"

    @classmethod
    def poll(cls, context):
        light_data = getattr(context, "light", None)
        return light_data is not None and light_data.phlp.has_ies

    def draw_header(self, context):
        self.layout.label(icon="OUTLINER_OB_LIGHT")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        props = context.light.phlp

        column = layout.column(align=True)
        column.prop(props, "halo_mode")

        body = layout.column()
        body.enabled = props.halo_mode != "NONE"

        column = body.column(align=True)
        column.prop(props, "halo_size")
        column.prop(props, "halo_opacity")
        column.prop(props, "halo_core")
        column.prop(props, "halo_falloff")

        column = body.column(align=True)
        column.prop(props, "halo_gamma")
        column.prop(props, "halo_resolution")
        column.prop(props, "halo_wireframe")

        column = body.column(align=True)
        column.prop(props, "halo_color_mode")
        sub = column.column()
        sub.enabled = props.halo_color_mode == "CUSTOM"
        sub.prop(props, "halo_color", text="Color")

        body.operator("phlp.copy_halo_settings", icon="DUPLICATE")


class PHLP_PT_view3d(Panel):
    """Sidebar panel docked in Blender's existing Tool tab, no extra tab."""

    bl_label = "Photometric Lights"
    bl_idname = "PHLP_PT_view3d"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Tool"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 100

    def draw(self, context):
        layout = self.layout

        layout.prop(context.scene, "phlp_show_halo", icon="HIDE_OFF")
        layout.operator("phlp.import_ies", icon="LIGHT_POINT",
                        text="Add Photometric Light")

        column = layout.column(align=True)
        operator = column.operator(
            "phlp.reload_photometry", icon="FILE_REFRESH", text="Reload All"
        )
        operator.all_lights = True
        column.operator("phlp.pack_all_ies", icon="PACKAGE")

        light_object = context.active_object
        if light_object is not None and light_object.type == "LIGHT":
            props = getattr(light_object.data, "phlp", None)
            if props is not None and props.has_ies:
                box = layout.box()
                box.label(text=light_object.name, icon="LIGHT_POINT")
                box.prop(props, "halo_mode", text="")
                box.prop(props, "halo_size")
                box.prop(props, "halo_opacity")


classes = (
    PHLP_PT_photometry,
    PHLP_PT_halo,
    PHLP_PT_view3d,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
