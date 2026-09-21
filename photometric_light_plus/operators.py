# ------------------------------------------------------------------------------------------
#  Copyright (c) Marco Caturano. All rights reserved.
#  Licensed under the GPLv3 License. See LICENSE in the project root for license information.
# ------------------------------------------------------------------------------------------
"""Operators.

Every operator is idempotent and self contained: no global locks, no timers, no
UI mutation outside of the operator execution, no object removal while iterating
a collection. That removes the whole class of race conditions the 1.x prototype
suffered from.
"""

import os

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import FileHandler, Operator
from bpy_extras import view3d_utils
from bpy_extras.io_utils import ImportHelper
from mathutils import Vector

from . import energy, ies_parser, overlay, storage

__all__ = ("classes", "menu_func", "register", "unregister")


def _drop_location(context, region_x, region_y):
    """Where a file dropped at the given region coordinates should land.

    Ray casts the visible geometry so a luminaire dropped on a ceiling sticks to
    it, and falls back to the plane through the 3D cursor when nothing is hit.
    The cast only reads the evaluated depsgraph, so it is safe inside invoke.
    """
    region = context.region
    region_3d = context.region_data
    cursor = context.scene.cursor.location.copy()
    if region is None or region_3d is None:
        return cursor

    coord = (region_x, region_y)
    try:
        origin = view3d_utils.region_2d_to_origin_3d(region, region_3d, coord)
        direction = view3d_utils.region_2d_to_vector_3d(region, region_3d, coord)
    except (AttributeError, ValueError):
        return cursor

    depsgraph = context.evaluated_depsgraph_get()
    hit, location, _normal, _index, _obj, _matrix = context.scene.ray_cast(
        depsgraph, origin, direction
    )
    if hit:
        return location.copy()

    # No geometry under the pointer: intersect the view ray with the horizontal
    # plane passing through the 3D cursor.
    if abs(direction.z) > 1e-6:
        distance = (cursor.z - origin.z) / direction.z
        if distance > 0.0:
            return origin + direction * distance

    return view3d_utils.region_2d_to_location_3d(
        region, region_3d, coord, Vector(cursor)
    )


def _iter_target_lights(context, selected_only):
    """Snapshot the light list before touching anything.

    Building the list up front avoids mutating a collection while iterating it.
    """
    if selected_only:
        objects = list(context.selected_objects)
    else:
        objects = list(context.view_layer.objects)

    seen = set()
    lights = []
    for obj in objects:
        if obj.type != "LIGHT" or obj.data is None:
            continue
        light_data = obj.data
        if light_data.session_uid in seen:
            continue
        seen.add(light_data.session_uid)
        lights.append(light_data)
    return lights


class PHLP_OT_import_ies(Operator, ImportHelper):
    """Create a point light driven by an IES or EULUMDAT photometric file"""

    bl_idname = "phlp.import_ies"
    bl_label = "Photometric Light (.ies / .ldt)"
    bl_description = (
        "Create a point light from an IES or EULUMDAT file. EULUMDAT files are "
        "converted to IES on import and stored inside the .blend"
    )
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".ies"
    filter_glob: StringProperty(
        default="*.ies;*.IES;*.ldt;*.LDT;*.eul;*.EUL;*.ltd;*.LTD",
        options={"HIDDEN"},
    )

    files: CollectionProperty(type=bpy.types.OperatorFileListElement,
                              options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN", "SKIP_SAVE"})

    use_file_name: BoolProperty(
        name="Name From Luminaire",
        description="Take the light name from the IES keyword block when available",
        default=True,
    )
    light_name: StringProperty(
        name="Light Name",
        description="Leave empty to use the file name",
        default="",
    )
    temperature: IntProperty(
        name="Temperature",
        description="Color temperature used when the IES file does not declare one",
        default=4000,
        min=1000,
        max=20000,
    )
    intensity_multiplier: FloatProperty(
        name="Intensity",
        description="Multiplier applied to the computed light power",
        default=1.0,
        min=0.0,
        soft_max=10.0,
    )
    halo_size: FloatProperty(
        name="Halo Size",
        description="Radius of the viewport halo, in meters",
        default=1.0,
        min=0.001,
        soft_max=20.0,
        subtype="DISTANCE",
    )
    align_to_cursor: BoolProperty(
        name="At 3D Cursor",
        description="Place the light at the 3D cursor instead of the world origin",
        default=True,
    )

    # Set by the drag and drop path only, never shown in the file browser.
    from_drop: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})
    drop_x: IntProperty(default=0, options={"HIDDEN", "SKIP_SAVE"})
    drop_y: IntProperty(default=0, options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        # A drop already carries the paths: import straight away instead of
        # opening a file browser the user did not ask for.
        if self.files and self.directory:
            self.from_drop = True
            self.drop_x = event.mouse_region_x
            self.drop_y = event.mouse_region_y
            return self.execute(context)
        self.from_drop = False
        return ImportHelper.invoke(self, context, event)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        column = layout.column(align=True)
        column.prop(self, "use_file_name")
        sub = column.column()
        sub.enabled = not self.use_file_name
        sub.prop(self, "light_name")

        column = layout.column(align=True)
        column.prop(self, "temperature")
        column.prop(self, "intensity_multiplier")

        column = layout.column(align=True)
        column.prop(self, "halo_size")
        column.prop(self, "align_to_cursor")

    def execute(self, context):
        paths = []
        if self.files and self.directory:
            for item in self.files:
                if item.name:
                    paths.append(os.path.join(self.directory, item.name))
        if not paths and self.filepath:
            paths.append(self.filepath)

        if not paths:
            self.report({"ERROR"}, "No IES file selected")
            return {"CANCELLED"}

        collection = context.collection
        if self.from_drop:
            location = _drop_location(context, self.drop_x, self.drop_y)
        elif self.align_to_cursor:
            location = context.scene.cursor.location.copy()
        else:
            location = (0.0, 0.0, 0.0)

        created = []
        converted = 0
        for path in paths:
            try:
                # EULUMDAT files are converted to LM-63 here, so only IES data
                # is ever stored inside the .blend.
                content, was_converted = storage.read_photometric_file(path)
                # Validate before creating anything, so a bad file leaves no
                # half built data-block behind.
                ies_parser.parse_ies_string(content)
            except (ies_parser.IESParseError, OSError) as error:
                self.report({"WARNING"}, "%s: %s" % (os.path.basename(path), error))
                continue
            converted += 1 if was_converted else 0

            base_name = energy.base_name_from_path(path)
            name = self.light_name.strip()
            if self.use_file_name or not name:
                name = energy.luminaire_name_from_content(content, base_name)

            light_data, _text = energy.build_light_data(
                content,
                base_name,
                light_name=name,
                temperature=float(self.temperature),
                intensity_multiplier=self.intensity_multiplier,
            )
            light_data.phlp.halo_size = self.halo_size

            light_object = bpy.data.objects.new(name, light_data)
            light_object.location = location
            collection.objects.link(light_object)
            created.append(light_object)

        if not created:
            return {"CANCELLED"}

        for obj in context.selected_objects:
            obj.select_set(False)
        for obj in created:
            obj.select_set(True)
        context.view_layer.objects.active = created[-1]

        overlay.tag_redraw_all()
        message = "Created %d photometric light(s)" % len(created)
        if converted:
            message += " (%d converted from EULUMDAT)" % converted
        self.report({"INFO"}, message)
        return {"FINISHED"}


class PHLP_OT_replace_ies(Operator, ImportHelper):
    """Replace the photometric payload of the active light"""

    bl_idname = "phlp.replace_ies"
    bl_label = "Replace Photometric File"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".ies"
    filter_glob: StringProperty(
        default="*.ies;*.IES;*.ldt;*.LDT;*.eul;*.EUL;*.ltd;*.LTD",
        options={"HIDDEN"},
    )

    @classmethod
    def poll(cls, context):
        return getattr(context, "light", None) is not None

    def execute(self, context):
        light_data = context.light
        try:
            content, _converted = storage.read_photometric_file(self.filepath)
            ies_parser.parse_ies_string(content)
        except (ies_parser.IESParseError, OSError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

        base_name = energy.base_name_from_path(self.filepath)
        text = storage.store_ies_text(content, base_name)

        props = light_data.phlp
        props.ies_text = text
        props.has_ies = True
        props.source_name = base_name
        props.web_version += 1

        node = storage.find_ies_node(light_data)
        if node is None:
            energy.setup_ies_nodes(light_data, text)
        else:
            node.mode = "INTERNAL"
            node.ies = text

        energy.apply_power(light_data)
        overlay.tag_redraw_all()
        self.report({"INFO"}, "IES replaced with %s" % base_name)
        return {"FINISHED"}


class PHLP_OT_reload_photometry(Operator):
    """Re-parse the internal IES payload and rebuild the halo"""

    bl_idname = "phlp.reload_photometry"
    bl_label = "Reload Photometry"
    bl_options = {"REGISTER", "UNDO"}

    all_lights: BoolProperty(
        name="All Lights",
        description="Process every light of the view layer instead of the active one",
        default=False,
    )

    def execute(self, context):
        if self.all_lights:
            targets = _iter_target_lights(context, selected_only=False)
        else:
            light_data = getattr(context, "light", None)
            targets = [light_data] if light_data is not None else []

        if not targets:
            self.report({"WARNING"}, "No light to process")
            return {"CANCELLED"}

        storage.clear_parse_cache()
        overlay.clear_batch_cache()

        processed = 0
        for light_data in targets:
            props = getattr(light_data, "phlp", None)
            if props is None:
                continue
            if not props.has_ies and not storage.ensure_internal_ies(light_data):
                continue
            props.web_version += 1
            energy.apply_power(light_data)
            processed += 1

        overlay.tag_redraw_all()
        self.report({"INFO"}, "Photometry reloaded on %d light(s)" % processed)
        return {"FINISHED"}


class PHLP_OT_pack_all_ies(Operator):
    """Make every IES light in the file fully self contained"""

    bl_idname = "phlp.pack_all_ies"
    bl_label = "Pack All IES Data"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        packed = 0
        skipped = 0
        # Snapshot first: never iterate bpy.data while modifying it.
        for light_data in list(bpy.data.lights):
            if storage.find_ies_node(light_data) is None:
                continue
            if storage.ensure_internal_ies(light_data):
                energy.apply_power(light_data)
                packed += 1
            else:
                skipped += 1

        storage.clear_parse_cache()
        overlay.clear_batch_cache()
        overlay.tag_redraw_all()
        self.report(
            {"INFO"}, "Packed %d light(s), %d could not be resolved" % (packed, skipped)
        )
        return {"FINISHED"}


class PHLP_OT_validate_ies(Operator):
    """Print a detailed photometric report to the system console"""

    bl_idname = "phlp.validate_ies"
    bl_label = "Validate IES Data"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return getattr(context, "light", None) is not None

    def execute(self, context):
        light_data = context.light
        data = storage.get_photometry(light_data)
        if data is None:
            self.report({"ERROR"}, "No valid IES payload on this light")
            return {"CANCELLED"}

        summary = data.summary()
        integrated = data.luminous_flux()
        declared = summary["declared_lumens"]

        print("=" * 78)
        print("Photometric Light Plus - report for %r" % light_data.name)
        print("  source file          : %s" % light_data.phlp.source_name)
        print("  photometric type     : %s" % summary["photometric_type"])
        print("  tilt                 : %s" % summary["tilt"])
        print("  vertical angles      : %d (%.1f .. %.1f)"
              % (summary["v_angles"], summary["v_range"][0], summary["v_range"][1]))
        print("  horizontal angles    : %d (%.1f .. %.1f)"
              % (summary["h_angles"], summary["h_range"][0], summary["h_range"][1]))
        print("  lateral symmetry     : %s" % summary["symmetry"])
        print("  max intensity        : %.1f cd" % summary["max_candela"])
        print("  beam angle (50%%)     : %.2f deg" % data.beam_angle())
        print("  luminous size        : %.3f x %.3f x %.3f m" % summary["size"])
        print("  declared lumens      : %.1f lm" % declared)
        print("  integrated lumens    : %.1f lm" % integrated)
        if declared > 0.0 and integrated > 0.0:
            print("  declared / integrated: %.3f" % (declared / integrated))
        print("  input watts          : %.1f W" % summary["input_watts"])
        print("  blender energy       : %.4f W" % light_data.energy)
        for warning in summary["warnings"]:
            print("  warning              : %s" % warning)
        print("=" * 78)

        delta = ""
        if declared > 0.0 and integrated > 0.0:
            delta = " (declared %.0f lm, ratio %.2f)" % (
                declared,
                declared / integrated,
            )
        self.report(
            {"INFO"},
            "Integrated %.0f lm%s - full report in the console" % (integrated, delta),
        )
        return {"FINISHED"}


class PHLP_OT_copy_halo_settings(Operator):
    """Copy the halo settings of the active light to every selected light"""

    bl_idname = "phlp.copy_halo_settings"
    bl_label = "Copy Halo Settings To Selected"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return getattr(context, "light", None) is not None

    def execute(self, context):
        source = context.light.phlp
        fields = (
            "halo_mode",
            "halo_size",
            "halo_opacity",
            "halo_falloff",
            "halo_core",
            "halo_gamma",
            "halo_resolution",
            "halo_wireframe",
            "halo_color_mode",
        )
        count = 0
        for light_data in _iter_target_lights(context, selected_only=True):
            if light_data is context.light:
                continue
            target = light_data.phlp
            for field in fields:
                setattr(target, field, getattr(source, field))
            target.halo_color = source.halo_color
            count += 1

        overlay.tag_redraw_all()
        self.report({"INFO"}, "Halo settings copied to %d light(s)" % count)
        return {"FINISHED"}


class PHLP_FH_import_ies(FileHandler):
    """Lets .ies and EULUMDAT files be dropped straight into the 3D viewport"""

    bl_idname = "PHLP_FH_import_ies"
    bl_label = "Photometric File (.ies / .ldt)"
    bl_import_operator = PHLP_OT_import_ies.bl_idname
    bl_file_extensions = ".ies;.ldt;.eul;.ltd"

    @classmethod
    def poll_drop(cls, context):
        area = getattr(context, "area", None)
        return area is not None and area.type == "VIEW_3D"


classes = (
    PHLP_OT_import_ies,
    PHLP_FH_import_ies,
    PHLP_OT_replace_ies,
    PHLP_OT_reload_photometry,
    PHLP_OT_pack_all_ies,
    PHLP_OT_validate_ies,
    PHLP_OT_copy_halo_settings,
)


def menu_func(self, context):
    self.layout.operator(PHLP_OT_import_ies.bl_idname, icon="LIGHT_POINT")


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
