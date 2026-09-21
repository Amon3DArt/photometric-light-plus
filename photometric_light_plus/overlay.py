# ------------------------------------------------------------------------------------------
#  Copyright (c) Marco Caturano. All rights reserved.
#  Licensed under the GPLv3 License. See LICENSE in the project root for license information.
# ------------------------------------------------------------------------------------------
"""Viewport halo overlay.

The photometric distribution is drawn straight into the 3D viewport, the same
way Blender draws the spot light cone: no object, no mesh, no curve, nothing to
parent, delete or keep in sync. Consequences that matter here:

* deleting a light removes its halo automatically,
* re-importing an IES only bumps a version counter,
* the halo is never evaluated by the dependency graph and never rendered.

Thread safety: the callback runs on the main thread inside the draw loop. It
only *reads* Blender data, never writes to ``bpy.data`` (writing from a draw
handler is undefined behaviour), and the batch cache is a plain dict touched
exclusively from that same thread.
"""

import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix

import bpy

from . import photometry as photometry_module
from . import storage

__all__ = (
    "clear_batch_cache",
    "register_handler",
    "tag_redraw_all",
    "unregister_handler",
)

_draw_handle = None
_shader = None

# key -> {"solid": GPUBatch, "wire": GPUBatch}
_BATCH_CACHE = {}
_BATCH_CACHE_LIMIT = 48

_VERTEX_SHADER = """
void main()
{
    v_position = pos;
    v_normal = nrm;
    v_intensity = intensity;
    gl_Position = ModelViewProjectionMatrix * vec4(pos, 1.0);
}
"""

_FRAGMENT_SHADER = """
void main()
{
    vec3 view_dir = (isPerspective > 0.5)
        ? normalize(viewPositionLocal - v_position)
        : normalize(viewDirectionLocal);

    float facing = abs(dot(normalize(v_normal), view_dir));
    float rim = pow(clamp(1.0 - facing, 0.0, 1.0), rimPower);

    float shape = clamp(v_intensity, 0.0, 1.0);
    float glow = (rim * 0.92 + coreGain) * pow(shape, 0.65);

    float alpha = clamp(glow * opacity, 0.0, 1.0);
    if (alpha < 0.002) {
        discard;
    }
    /* Premultiplied output, drawn with additive premultiplied blending. */
    fragColor = vec4(baseColor * alpha, alpha);
}
"""


def _ensure_shader():
    """Build the halo shader lazily, using the backend agnostic create-info API."""
    global _shader
    if _shader is not None:
        return _shader

    interface = gpu.types.GPUStageInterfaceInfo("phlp_halo_interface")
    interface.smooth("VEC3", "v_position")
    interface.smooth("VEC3", "v_normal")
    interface.smooth("FLOAT", "v_intensity")

    info = gpu.types.GPUShaderCreateInfo()
    info.push_constant("MAT4", "ModelViewProjectionMatrix")
    info.push_constant("VEC3", "viewPositionLocal")
    info.push_constant("VEC3", "viewDirectionLocal")
    info.push_constant("VEC3", "baseColor")
    info.push_constant("FLOAT", "opacity")
    info.push_constant("FLOAT", "rimPower")
    info.push_constant("FLOAT", "coreGain")
    info.push_constant("FLOAT", "isPerspective")
    info.vertex_in(0, "VEC3", "pos")
    info.vertex_in(1, "VEC3", "nrm")
    info.vertex_in(2, "FLOAT", "intensity")
    info.vertex_out(interface)
    info.fragment_out(0, "VEC4", "fragColor")
    info.vertex_source(_VERTEX_SHADER)
    info.fragment_source(_FRAGMENT_SHADER)

    _shader = gpu.shader.create_from_info(info)
    del info
    del interface
    return _shader


def clear_batch_cache():
    """Drop every cached GPU batch, e.g. on file load, undo or unregister."""
    _BATCH_CACHE.clear()


def tag_redraw_all():
    """Ask every 3D viewport for a redraw after a property change."""
    context = bpy.context
    window_manager = getattr(context, "window_manager", None)
    if window_manager is None:
        return
    for window in window_manager.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _get_batches(light_data, props):
    key = (
        light_data.session_uid,
        props.web_version,
        props.halo_resolution,
        round(props.halo_gamma, 3),
    )
    cached = _BATCH_CACHE.get(key)
    if cached is not None:
        return cached

    data = storage.get_photometry(light_data)
    if data is None:
        _BATCH_CACHE[key] = None
        return None

    shader = _ensure_shader()
    try:
        positions, normals, intensities, triangles, lines = (
            photometry_module.build_halo_geometry(
                data,
                resolution=props.halo_resolution,
                gamma=props.halo_gamma,
            )
        )
        attributes = {
            "pos": positions.tolist(),
            "nrm": normals.tolist(),
            "intensity": intensities.tolist(),
        }
        entry = {
            "solid": batch_for_shader(
                shader, "TRIS", attributes, indices=triangles.tolist()
            ),
            "wire": batch_for_shader(
                shader, "LINES", attributes, indices=lines.tolist()
            ),
        }
    except Exception as error:  # noqa: BLE001 - never break the draw loop
        print("[Photometric Light Plus] halo build failed: %s" % error)
        _BATCH_CACHE[key] = None
        return None

    if len(_BATCH_CACHE) >= _BATCH_CACHE_LIMIT:
        _BATCH_CACHE.clear()
    _BATCH_CACHE[key] = entry
    return entry


def _halo_color(light_data, props):
    if props.halo_color_mode == "CUSTOM":
        return tuple(props.halo_color)

    color = tuple(light_data.color)
    peak = max(color) or 1.0
    # Normalize so the halo brightness only depends on the opacity setting.
    return (color[0] / peak, color[1] / peak, color[2] / peak)


def _iter_visible_lights(context, space):
    """Yield the light objects visible in the viewport currently being drawn."""
    view_layer = context.view_layer
    for obj in view_layer.objects:
        if obj.type != "LIGHT" or obj.data is None:
            continue
        # ``viewport`` also accounts for local view and per viewport exclusions.
        if not obj.visible_get(view_layer=view_layer, viewport=space):
            continue
        yield obj


def _draw_callback():
    context = bpy.context

    space = context.space_data
    if space is None or space.type != "VIEW_3D":
        return
    if not space.overlay.show_overlays:
        return

    scene = context.scene
    if scene is None or not getattr(scene, "phlp_show_halo", True):
        return

    region_3d = context.region_data
    if region_3d is None:
        return

    try:
        lights = list(_iter_visible_lights(context, space))
    except (AttributeError, ReferenceError):
        return
    if not lights:
        return

    shader = _ensure_shader()

    view_matrix_inv = region_3d.view_matrix.inverted()
    view_location = view_matrix_inv.translation
    view_forward = -view_matrix_inv.col[2].to_3d()
    is_perspective = 1.0 if region_3d.is_perspective else 0.0

    state_pushed = False

    for obj in lights:
        light_data = obj.data
        props = getattr(light_data, "phlp", None)
        if props is None or not props.has_ies:
            continue
        if props.halo_mode == "NONE":
            continue
        if props.halo_mode == "SELECTED" and not obj.select_get():
            continue

        batches = _get_batches(light_data, props)
        if not batches:
            continue

        # Uniform scale only: the halo size stays independent of object scale.
        size = max(1e-4, props.halo_size)
        matrix = (
            Matrix.Translation(obj.matrix_world.translation)
            @ obj.matrix_world.to_quaternion().to_matrix().to_4x4()
            @ Matrix.Scale(size, 4)
        )
        try:
            matrix_inv = matrix.inverted()
        except ValueError:
            continue

        if not state_pushed:
            gpu.state.blend_set("ADDITIVE_PREMULT")
            gpu.state.depth_test_set("LESS_EQUAL")
            gpu.state.depth_mask_set(False)
            gpu.state.face_culling_set("NONE")
            state_pushed = True

        color = _halo_color(light_data, props)

        gpu.matrix.push()
        gpu.matrix.multiply_matrix(matrix)

        shader.bind()
        shader.uniform_float("viewPositionLocal", matrix_inv @ view_location)
        shader.uniform_float(
            "viewDirectionLocal", (matrix_inv.to_3x3() @ view_forward).normalized()
        )
        shader.uniform_float("baseColor", color)
        shader.uniform_float("rimPower", props.halo_falloff)
        shader.uniform_float("coreGain", props.halo_core)
        shader.uniform_float("isPerspective", is_perspective)

        shader.uniform_float("opacity", props.halo_opacity)
        batches["solid"].draw(shader)

        if props.halo_wireframe:
            # Single pixel lines only: since 4.5 wide lines require the builtin
            # POLYLINE shader variants, which a custom shader cannot use.
            shader.uniform_float("opacity", props.halo_opacity * 0.6)
            shader.uniform_float("coreGain", max(props.halo_core, 0.35))
            batches["wire"].draw(shader)
            shader.uniform_float("coreGain", props.halo_core)

        gpu.matrix.pop()

    if state_pushed:
        gpu.state.depth_mask_set(True)
        gpu.state.depth_test_set("NONE")
        gpu.state.blend_set("NONE")


def register_handler():
    global _draw_handle
    if _draw_handle is not None:
        return
    _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
        _draw_callback, (), "WINDOW", "POST_VIEW"
    )


def unregister_handler():
    global _draw_handle
    global _shader
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, "WINDOW")
        _draw_handle = None
    clear_batch_cache()
    _shader = None
