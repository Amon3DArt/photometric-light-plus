# ------------------------------------------------------------------------------------------
#  Copyright (c) Marco Caturano. All rights reserved.
#  Licensed under the GPLv3 License. See LICENSE in the project root for license information.
# ------------------------------------------------------------------------------------------
"""Light data-block construction, node graph setup and photometric power."""

import math
import os

import bpy

from . import photometry as photometry_module
from . import storage

__all__ = (
    "apply_power",
    "build_light_data",
    "refresh_photometry_info",
    "setup_ies_nodes",
)

DEFAULT_TEMPERATURE = 4000.0


def ensure_node_tree(light_data):
    """Return the light node tree, creating it when the build still needs it.

    Blender 5.1 removed ``Light.use_nodes``: lights always own a node tree and
    setting the property has no effect. On 4.5 the tree only exists once the
    property is enabled, so it is only touched when the tree is missing.
    """
    if light_data.node_tree is None and hasattr(light_data, "use_nodes"):
        light_data.use_nodes = True
    return light_data.node_tree


def setup_ies_nodes(light_data, text):
    """Create the minimal Emission + IES graph, replacing any existing one."""
    tree = ensure_node_tree(light_data)
    if tree is None:
        return None

    nodes = tree.nodes
    links = tree.links
    nodes.clear()

    output_node = nodes.new("ShaderNodeOutputLight")
    output_node.location = (420.0, 0.0)
    emission_node = nodes.new("ShaderNodeEmission")
    emission_node.location = (200.0, 0.0)
    ies_node = nodes.new("ShaderNodeTexIES")
    ies_node.location = (-40.0, -60.0)
    ies_node.mode = "INTERNAL"
    ies_node.ies = text

    strength_output = ies_node.outputs.get("Fac") or ies_node.outputs[0]
    links.new(strength_output, emission_node.inputs["Strength"])
    links.new(emission_node.outputs["Emission"], output_node.inputs["Surface"])
    return ies_node


def _set_temperature(light_data, cct):
    """Apply a blackbody color temperature when the build supports it."""
    if cct is None:
        return
    if hasattr(light_data, "use_temperature"):
        light_data.use_temperature = True
        light_data.temperature = float(
            max(800.0, min(float(cct), 20000.0))
        )


def _exposure_factor(light_data):
    """Blender 5.x scales the light power by ``2 ** exposure``."""
    exposure = getattr(light_data, "exposure", 0.0)
    try:
        return 2.0 ** float(exposure)
    except (TypeError, ValueError, OverflowError):
        return 1.0


def _light_cct(light_data):
    if getattr(light_data, "use_temperature", False):
        return float(light_data.temperature)
    return DEFAULT_TEMPERATURE


def resolve_efficacy(light_data):
    """Return the lm/W divisor selected for this light."""
    props = light_data.phlp
    mode = props.efficacy_mode
    if mode == "SPECTRAL":
        return photometry_module.luminous_efficacy_of_radiation(
            _light_cct(light_data)
        )
    if mode == "CUSTOM":
        return max(1.0, props.custom_efficacy)
    return photometry_module.MAX_LUMINOUS_EFFICACY


def refresh_photometry_info(light_data):
    """Re-read the internal payload and cache the derived numbers on the light."""
    props = light_data.phlp
    data = storage.get_photometry(light_data)
    if data is None:
        props.info_line = "No valid IES data"
        return None

    declared = data.lumens_per_lamp * max(1, data.num_lamps)
    if declared <= 0.0:
        declared = 0.0

    integrated = data.luminous_flux()

    props.declared_lumens = declared
    props.max_candela = data.max_candela
    props.beam_angle = data.beam_angle()

    summary = data.summary()
    props.info_line = (
        "Type %s | %d V x %d H | V %.0f-%.0f | %.0f cd max | beam %.1f deg"
        % (
            summary["photometric_type"],
            summary["v_angles"],
            summary["h_angles"],
            summary["v_range"][0],
            summary["v_range"][1],
            summary["max_candela"],
            props.beam_angle,
        )
    )

    props.computed_lumens = (
        declared if (props.flux_source == "DECLARED" and declared > 0.0) else integrated
    )

    for warning in summary["warnings"]:
        print("[Photometric Light Plus] %s: %s" % (light_data.name, warning))

    return data


def apply_power(light_data):
    """Recompute ``Light.energy`` from the photometric flux."""
    props = light_data.phlp
    if not props.has_ies or props.flux_source == "MANUAL":
        return

    data = refresh_photometry_info(light_data)
    if data is None:
        return

    if props.flux_source == "ABSOLUTE":
        # The IES node already carries the absolute candela values, so the
        # light power is a pure multiplier. See photometry.absolute_watts.
        watts = photometry_module.absolute_watts(props.intensity_multiplier)
    else:
        watts = photometry_module.watts_from_lumens(
            props.computed_lumens,
            resolve_efficacy(light_data),
            props.intensity_multiplier,
        )
    if watts > 0.0:
        # Compensate the 5.x exposure slider so the emitted flux stays correct.
        light_data.energy = watts / _exposure_factor(light_data)


def build_light_data(content, base_name, light_name=None, temperature=None,
                     intensity_multiplier=1.0):
    """Create a fully configured point light data-block from an IES payload."""
    text = storage.store_ies_text(content, base_name)

    light_data = bpy.data.lights.new(name=light_name or base_name, type="POINT")
    props = light_data.phlp
    props.ies_text = text
    props.has_ies = True
    props.source_name = base_name
    props.web_version += 1
    props.intensity_multiplier = intensity_multiplier

    setup_ies_nodes(light_data, text)

    # Start from a neutral exposure on Blender 5.x so the computed wattage is
    # the actual emitted power.
    if hasattr(light_data, "exposure"):
        light_data.exposure = 0.0

    data = storage.get_photometry(light_data)
    cct = temperature
    if data is not None:
        file_cct = data.color_temperature()
        if cct is None:
            cct = file_cct
        # Use the luminous opening to get a plausible soft shadow radius.
        width, length, height = data.luminous_size()
        radius = max(width, length, height) * 0.5
        if 0.0 < radius < 2.0:
            light_data.shadow_soft_size = radius
    _set_temperature(light_data, cct if cct else DEFAULT_TEMPERATURE)

    apply_power(light_data)
    return light_data, text


def base_name_from_path(filepath):
    return os.path.splitext(os.path.basename(filepath))[0] or "IES"


def luminaire_name_from_content(content, fallback):
    """Pick a friendly name from the IES keyword block."""
    for line in content.splitlines()[:40]:
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("[LUMINAIRE]") or upper.startswith("[LUMCAT]"):
            value = stripped.split("]", 1)[-1].strip()
            if value:
                return value[:60]
        if upper.startswith("TILT"):
            break
    return fallback


def estimate_lux_at(distance, candela):
    """Small helper exposed for the UI: illuminance at a given distance."""
    if distance <= 0.0:
        return 0.0
    return candela / (distance * distance)


def degrees_or_zero(value):
    return math.degrees(value) if value else 0.0
