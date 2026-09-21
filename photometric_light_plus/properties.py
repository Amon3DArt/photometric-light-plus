# ------------------------------------------------------------------------------------------
#  Copyright (c) Marco Caturano. All rights reserved.
#  Licensed under the GPLv3 License. See LICENSE in the project root for license information.
# ------------------------------------------------------------------------------------------
"""Per light settings, stored on the light data-block so they follow the .blend."""

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Light, PropertyGroup, Scene

from . import energy, overlay

__all__ = ("PHLP_LightProperties", "classes", "register", "unregister")


def _redraw(self, context):
    overlay.tag_redraw_all()


def _update_power(self, context):
    """Recompute the light wattage when the user tweaks the multiplier.

    Writing to the owning data-block from its own update callback is safe: the
    callback runs from the UI/RNA layer, never from the dependency graph.
    """
    light_data = self.id_data
    if isinstance(light_data, bpy.types.Light):
        energy.apply_power(light_data)
    overlay.tag_redraw_all()


class PHLP_LightProperties(PropertyGroup):
    # -- internal IES payload ------------------------------------------------
    has_ies: BoolProperty(
        name="Has IES",
        description="True when this light carries an internal IES payload",
        default=False,
    )
    ies_text: PointerProperty(
        name="IES Data",
        description="Internal text data-block holding the IES payload",
        type=bpy.types.Text,
    )
    source_name: StringProperty(
        name="Source File",
        description="Original file name, kept for reference only",
        default="",
    )
    web_version: IntProperty(
        name="Photometry Version",
        description="Bumped whenever the IES payload changes, invalidates caches",
        default=0,
    )

    # -- power ---------------------------------------------------------------
    flux_source: EnumProperty(
        name="Power Mode",
        description="How the light power is derived from the photometric data",
        items=(
            (
                "ABSOLUTE",
                "Absolute IES",
                "Let the IES node carry the absolute candela values and keep "
                "the light power as a pure multiplier. Physically exact. "
                "Recommended",
            ),
            (
                "INTEGRATED",
                "Integrated",
                "Integrate the candela distribution over the sphere (recommended)",
            ),
            (
                "DECLARED",
                "Declared",
                "Use the lumens declared in the IES header",
            ),
            ("MANUAL", "Manual", "Use the wattage currently set on the light"),
        ),
        default="ABSOLUTE",
        update=_update_power,
    )
    intensity_multiplier: FloatProperty(
        name="Intensity",
        description="Multiplier applied to the computed light power",
        default=1.0,
        min=0.0,
        soft_max=10.0,
        update=_update_power,
    )
    efficacy_mode: EnumProperty(
        name="Efficacy",
        description="Luminous efficacy used to turn lumens into Blender watts",
        items=(
            (
                "STANDARD",
                "Blender (683 lm/W)",
                "Maximum luminous efficacy, consistent with how Blender reads "
                "watts as photometric units. Recommended",
            ),
            (
                "SPECTRAL",
                "Spectral (LER)",
                "Luminous efficacy of radiation for the light color temperature, "
                "models the radiant power of a real lamp and gives a much "
                "higher wattage",
            ),
            ("CUSTOM", "Custom", "Use a user defined lm/W value"),
        ),
        default="STANDARD",
        update=_update_power,
    )
    custom_efficacy: FloatProperty(
        name="lm/W",
        description="Custom luminous efficacy",
        default=683.0,
        min=1.0,
        soft_max=683.0,
        update=_update_power,
    )
    computed_lumens: FloatProperty(
        name="Computed Lumens",
        description="Luminous flux used for the power computation",
        default=0.0,
    )
    declared_lumens: FloatProperty(
        name="Declared Lumens",
        description="Luminous flux declared in the IES header",
        default=0.0,
    )
    max_candela: FloatProperty(name="Max Candela", default=0.0)
    beam_angle: FloatProperty(name="Beam Angle", default=0.0)
    info_line: StringProperty(name="Photometry Info", default="")

    # -- halo overlay --------------------------------------------------------
    halo_mode: EnumProperty(
        name="Halo",
        description="When the photometric halo is drawn in the viewport",
        items=(
            ("NONE", "Off", "Never draw the halo"),
            ("SELECTED", "Selected", "Draw the halo only while the light is selected"),
            ("ALWAYS", "Always", "Always draw the halo"),
        ),
        default="SELECTED",
        update=_redraw,
    )
    halo_size: FloatProperty(
        name="Size",
        description="Radius of the halo at maximum intensity, in meters",
        default=1.0,
        min=0.001,
        soft_max=20.0,
        subtype="DISTANCE",
        update=_redraw,
    )
    halo_opacity: FloatProperty(
        name="Opacity",
        description="Overall strength of the halo",
        default=0.55,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=_redraw,
    )
    halo_falloff: FloatProperty(
        name="Rim Falloff",
        description="Higher values concentrate the glow on the silhouette",
        default=2.2,
        min=0.25,
        max=8.0,
        update=_redraw,
    )
    halo_core: FloatProperty(
        name="Core",
        description="Base opacity of the surfaces facing the viewer",
        default=0.08,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=_redraw,
    )
    halo_gamma: FloatProperty(
        name="Shape Gamma",
        description="Shapes the radius response, higher values inflate weak lobes",
        default=1.0,
        min=0.2,
        max=4.0,
        update=_redraw,
    )
    halo_resolution: EnumProperty(
        name="Resolution",
        description="Tessellation density of the halo",
        items=(
            ("LOW", "Low", "32 x 48 segments"),
            ("MEDIUM", "Medium", "48 x 72 segments"),
            ("HIGH", "High", "72 x 120 segments"),
        ),
        default="MEDIUM",
        update=_redraw,
    )
    halo_wireframe: BoolProperty(
        name="Wireframe",
        description="Overlay meridians and parallels on the halo",
        default=True,
        update=_redraw,
    )
    halo_color_mode: EnumProperty(
        name="Color",
        description="Source of the halo color",
        items=(
            ("LIGHT", "Light", "Use the light color"),
            ("CUSTOM", "Custom", "Use a custom color"),
        ),
        default="LIGHT",
        update=_redraw,
    )
    halo_color: FloatVectorProperty(
        name="Halo Color",
        subtype="COLOR",
        size=3,
        min=0.0,
        max=1.0,
        default=(1.0, 0.85, 0.55),
        update=_redraw,
    )


classes = (PHLP_LightProperties,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    Light.phlp = PointerProperty(type=PHLP_LightProperties)
    Scene.phlp_show_halo = BoolProperty(
        name="Show Photometric Halos",
        description="Master switch for every photometric halo in the viewport",
        default=True,
        update=_redraw,
    )


def unregister():
    if hasattr(Scene, "phlp_show_halo"):
        del Scene.phlp_show_halo
    if hasattr(Light, "phlp"):
        del Light.phlp

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
