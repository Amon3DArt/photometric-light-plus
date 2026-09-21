# ------------------------------------------------------------------------------------------
#  Copyright (c) Marco Caturano. All rights reserved.
#  Licensed under the GPLv3 License. See LICENSE in the project root for license information.
# ------------------------------------------------------------------------------------------
"""EULUMDAT (.ldt) reader and EULUMDAT to IESNA LM-63 converter.

Pure python, no Blender dependency. The add-on never keeps EULUMDAT data at
runtime: a dropped .ldt file is converted to an LM-63 payload straight away and
only that payload is stored inside the .blend, so the rest of the pipeline (the
IES texture node, the halo, the flux integration) stays on a single format.

EULUMDAT record layout used here (one value per line for records 1 to 26, then
free form numeric blocks):

    1       company / databank / version
    2       Ityp   type indicator
    3       Isym   symmetry indicator
    4       Mc     number of C planes
    5       Dc     distance between C planes
    6       Ng     number of intensities per C plane
    7       Dg     distance between intensities
    8       measurement report number
    9       luminaire name
    10      luminaire number
    11      file name
    12      date / user
    13-15   luminaire length/diameter, width, height (mm)
    16-21   luminous area length/diameter, width, heights C0/C90/C180/C270 (mm)
    22      DFF    downward flux fraction (%)
    23      LORL   light output ratio of the luminaire (%)
    24      conversion factor for luminous intensities
    25      tilt during measurement
    26      n      number of standard lamp sets, then 6 lines per set
    27      10 direct ratios
    28      Mc C plane angles
    29      Ng gamma angles
    30      (Mc2 - Mc1 + 1) * Ng intensities, in cd/1000 lm
"""

import math
import re

__all__ = (
    "LDTParseError",
    "LDTPhotometry",
    "convert_ldt_to_ies",
    "looks_like_ldt",
    "parse_ldt_string",
)

_TOKEN_SPLIT = re.compile(r"[,;\s]+")

# Symmetry indicators.
SYM_NONE = 0
SYM_VERTICAL_AXIS = 1
SYM_C0_C180 = 2
SYM_C90_C270 = 3
SYM_BOTH_PLANES = 4


class LDTParseError(Exception):
    """Raised when the EULUMDAT payload cannot be interpreted."""


class LDTPhotometry:
    """Parsed EULUMDAT data, already expanded to a full set of C planes."""

    __slots__ = (
        "company",
        "type_indicator",
        "symmetry",
        "num_c_planes",
        "num_gamma",
        "report_number",
        "luminaire_name",
        "luminaire_number",
        "file_name",
        "date_user",
        "luminaire_length",
        "luminaire_width",
        "luminaire_height",
        "area_length",
        "area_width",
        "area_height_c0",
        "area_height_c90",
        "area_height_c180",
        "area_height_c270",
        "downward_flux_fraction",
        "light_output_ratio",
        "conversion_factor",
        "measurement_tilt",
        "num_lamps",
        "lamp_type",
        "total_flux",
        "color_appearance",
        "color_rendering",
        "wattage",
        "absolute_photometry",
        "c_angles",
        "gamma_angles",
        "candela",
        "warnings",
    )

    def __init__(self):
        self.company = ""
        self.type_indicator = 0
        self.symmetry = SYM_NONE
        self.num_c_planes = 0
        self.num_gamma = 0
        self.report_number = ""
        self.luminaire_name = ""
        self.luminaire_number = ""
        self.file_name = ""
        self.date_user = ""
        self.luminaire_length = 0.0
        self.luminaire_width = 0.0
        self.luminaire_height = 0.0
        self.area_length = 0.0
        self.area_width = 0.0
        self.area_height_c0 = 0.0
        self.area_height_c90 = 0.0
        self.area_height_c180 = 0.0
        self.area_height_c270 = 0.0
        self.downward_flux_fraction = 0.0
        self.light_output_ratio = 0.0
        self.conversion_factor = 1.0
        self.measurement_tilt = 0.0
        self.num_lamps = 1
        self.lamp_type = ""
        self.total_flux = 0.0
        self.color_appearance = ""
        self.color_rendering = ""
        self.wattage = 0.0
        self.absolute_photometry = False
        # Expanded to the full C plane list, in absolute candela.
        self.c_angles = []
        self.gamma_angles = []
        self.candela = []  # candela[c_index][gamma_index]
        self.warnings = []

    def max_candela(self):
        peak = 0.0
        for row in self.candela:
            for value in row:
                if value > peak:
                    peak = value
        return peak

    def luminous_size_meters(self):
        """Luminous area size as ``(width, length, height)`` in meters.

        A zero width means a circular opening, which LM-63 encodes with
        negative dimensions.
        """
        length = self.area_length / 1000.0
        width = self.area_width / 1000.0
        height = max(
            self.area_height_c0,
            self.area_height_c90,
            self.area_height_c180,
            self.area_height_c270,
        ) / 1000.0
        if width <= 0.0:
            return -length, -length, height
        return width, length, height


def _plane_count_range(symmetry, num_c_planes):
    """Return the ``(first, last)`` 1 based C plane indices stored in the file."""
    if symmetry == SYM_VERTICAL_AXIS:
        return 1, 1
    if symmetry == SYM_C0_C180:
        return 1, num_c_planes // 2 + 1
    if symmetry == SYM_C90_C270:
        # Runs from C270 through C0 up to C90, so the indices wrap past Mc.
        first = (3 * num_c_planes) // 4 + 1
        return first, first + num_c_planes // 2
    if symmetry == SYM_BOTH_PLANES:
        return 1, num_c_planes // 4 + 1
    return 1, num_c_planes


def _mirror_angles(angle, symmetry):
    """Every C angle that carries the same intensity row."""
    angle = angle % 360.0
    mirrored = [angle]
    if symmetry == SYM_C0_C180:
        mirrored.append((360.0 - angle) % 360.0)
    elif symmetry == SYM_C90_C270:
        mirrored.append((180.0 - angle) % 360.0)
    elif symmetry == SYM_BOTH_PLANES:
        mirrored.append((360.0 - angle) % 360.0)
        mirrored.append((180.0 - angle) % 360.0)
        mirrored.append((180.0 + angle) % 360.0)
    return mirrored


def looks_like_ldt(content):
    """Cheap format sniffing, used when the extension is not conclusive."""
    if not content:
        return False
    head = content[:4096].upper()
    if "TILT=" in head or "IESNA" in head:
        return False
    lines = [line.strip() for line in content.splitlines()]
    if len(lines) < 30:
        return False
    # Records 2 to 7 are small integers, which is a strong EULUMDAT signature.
    try:
        type_indicator = int(float(lines[1]))
        symmetry = int(float(lines[2]))
        num_c = int(float(lines[3]))
        num_g = int(float(lines[5]))
    except (ValueError, IndexError):
        return False
    return (
        0 <= type_indicator <= 3
        and 0 <= symmetry <= 4
        and 1 <= num_c <= 720
        and 1 <= num_g <= 720
    )


def parse_ldt_string(text):
    """Parse an EULUMDAT payload held in memory."""
    if not text:
        raise LDTParseError("Empty EULUMDAT data")

    lines = [line.strip() for line in text.splitlines()]
    if len(lines) < 30:
        raise LDTParseError("Truncated EULUMDAT data")

    data = LDTPhotometry()

    def header(index, what):
        if index >= len(lines):
            raise LDTParseError("Truncated EULUMDAT data while reading %s" % what)
        return lines[index]

    def header_float(index, what, default=0.0):
        value = header(index, what)
        if not value:
            return default
        try:
            return float(value.replace(",", "."))
        except ValueError:
            data.warnings.append("Record %d (%s) is not numeric: %r"
                                 % (index + 1, what, value))
            return default

    def header_int(index, what, default=0):
        return int(header_float(index, what, float(default)))

    data.company = header(0, "company")
    data.type_indicator = header_int(1, "type indicator")
    data.symmetry = header_int(2, "symmetry indicator")
    data.num_c_planes = header_int(3, "number of C planes")
    header_float(4, "distance between C planes")
    data.num_gamma = header_int(5, "number of intensities per C plane")
    header_float(6, "distance between intensities")
    data.report_number = header(7, "measurement report number")
    data.luminaire_name = header(8, "luminaire name")
    data.luminaire_number = header(9, "luminaire number")
    data.file_name = header(10, "file name")
    data.date_user = header(11, "date and user")
    data.luminaire_length = header_float(12, "luminaire length")
    data.luminaire_width = header_float(13, "luminaire width")
    data.luminaire_height = header_float(14, "luminaire height")
    data.area_length = header_float(15, "luminous area length")
    data.area_width = header_float(16, "luminous area width")
    data.area_height_c0 = header_float(17, "luminous area height C0")
    data.area_height_c90 = header_float(18, "luminous area height C90")
    data.area_height_c180 = header_float(19, "luminous area height C180")
    data.area_height_c270 = header_float(20, "luminous area height C270")
    data.downward_flux_fraction = header_float(21, "downward flux fraction")
    data.light_output_ratio = header_float(22, "light output ratio")
    data.conversion_factor = header_float(23, "conversion factor", 1.0)
    data.measurement_tilt = header_float(24, "measurement tilt")
    lamp_sets = header_int(25, "number of lamp sets", 1)

    if data.num_c_planes < 1 or data.num_gamma < 1:
        raise LDTParseError(
            "Invalid angle counts (%d C planes, %d gamma angles)"
            % (data.num_c_planes, data.num_gamma)
        )
    if data.symmetry not in (0, 1, 2, 3, 4):
        data.warnings.append(
            "Unknown symmetry indicator %d, assuming none" % data.symmetry
        )
        data.symmetry = SYM_NONE

    # A negative set count marks absolute photometry in several exporters.
    if lamp_sets < 0:
        data.absolute_photometry = True
        lamp_sets = abs(lamp_sets)
    lamp_sets = max(1, lamp_sets)

    cursor = 26
    for set_index in range(lamp_sets):
        num_lamps_raw = header(cursor, "number of lamps")
        try:
            num_lamps = int(float(num_lamps_raw))
        except ValueError:
            num_lamps = 1
        lamp_type = header(cursor + 1, "lamp type")
        total_flux = header_float(cursor + 2, "total luminous flux")
        color_appearance = header(cursor + 3, "color appearance")
        color_rendering = header(cursor + 4, "color rendering")
        wattage = header_float(cursor + 5, "wattage")
        if set_index == 0:
            # A negative lamp count or flux also marks absolute photometry.
            if num_lamps < 0 or total_flux < 0.0:
                data.absolute_photometry = True
            data.num_lamps = max(1, abs(num_lamps))
            data.lamp_type = lamp_type
            data.total_flux = abs(total_flux)
            data.color_appearance = color_appearance
            data.color_rendering = color_rendering
            data.wattage = abs(wattage)
        cursor += 6

    # Everything past the lamp sets is a free form numeric block.
    tokens = []
    for line in lines[cursor:]:
        if not line:
            continue
        for token in _TOKEN_SPLIT.split(line):
            if token:
                tokens.append(token)

    position = 0
    token_count = len(tokens)

    def next_float(what):
        nonlocal position
        while position < token_count:
            token = tokens[position]
            position += 1
            try:
                return float(token.replace(",", "."))
            except ValueError:
                data.warnings.append(
                    "Ignored non numeric token %r before %s" % (token, what)
                )
        raise LDTParseError("Truncated EULUMDAT data while reading %s" % what)

    for _ in range(10):
        next_float("direct ratio")

    c_angles = [next_float("C plane angle") for _ in range(data.num_c_planes)]
    gamma_angles = [next_float("gamma angle") for _ in range(data.num_gamma)]

    first_plane, last_plane = _plane_count_range(data.symmetry, data.num_c_planes)
    stored_planes = last_plane - first_plane + 1
    if stored_planes < 1 or stored_planes > data.num_c_planes + 1:
        raise LDTParseError(
            "Symmetry %d is inconsistent with %d C planes"
            % (data.symmetry, data.num_c_planes)
        )

    stored = []
    for _ in range(stored_planes):
        stored.append([next_float("intensity") for _ in range(data.num_gamma)])

    if position < token_count:
        data.warnings.append(
            "%d trailing token(s) ignored after the intensity block"
            % (token_count - position)
        )

    # cd/1000 lm -> absolute candela.
    if data.absolute_photometry:
        scale = 1.0
    else:
        if data.total_flux <= 0.0:
            raise LDTParseError("The lamp luminous flux is missing or zero")
        scale = data.total_flux / 1000.0
    if data.conversion_factor > 0.0:
        scale *= data.conversion_factor
        if abs(data.conversion_factor - 1.0) > 1e-6:
            data.warnings.append(
                "Applied intensity conversion factor %.4f" % data.conversion_factor
            )

    # Expand the stored planes over the full 360 degrees.
    expanded = {}
    for offset in range(stored_planes):
        # Symmetry 3 wraps around C360, so the plane index is taken modulo Mc.
        angle = c_angles[(first_plane - 1 + offset) % data.num_c_planes]
        row = [value * scale for value in stored[offset]]
        for mirrored in _mirror_angles(angle, data.symmetry):
            expanded.setdefault(round(mirrored % 360.0, 3), row)

    if data.symmetry == SYM_VERTICAL_AXIS:
        output_angles = [0.0]
    else:
        output_angles = [angle % 360.0 for angle in c_angles]
        # Close the loop so readers interpolate correctly between the last
        # plane and C0.
        if output_angles and abs(output_angles[-1] - 360.0) > 1e-6:
            output_angles.append(360.0)

    keys = sorted(expanded.keys())
    if not keys:
        raise LDTParseError("No intensity data found")

    candela = []
    for angle in output_angles:
        key = round(angle % 360.0, 3)
        row = expanded.get(key)
        if row is None:
            # Nearest stored plane, wrapping around 360 degrees.
            nearest = min(
                keys,
                key=lambda stored_key: min(
                    abs(stored_key - key), 360.0 - abs(stored_key - key)
                ),
            )
            row = expanded[nearest]
        candela.append(list(row))

    data.c_angles = output_angles
    data.gamma_angles = gamma_angles
    data.candela = candela

    if data.max_candela() <= 0.0:
        raise LDTParseError("The EULUMDAT file contains no positive intensity")

    return data


def _format_number(value):
    if abs(value - round(value)) < 1e-9:
        return "%d" % int(round(value))
    return ("%.6f" % value).rstrip("0").rstrip(".")


def _wrap_values(values, per_line=12):
    out = []
    for start in range(0, len(values), per_line):
        out.append(" ".join(_format_number(v) for v in values[start:start + per_line]))
    return out


def convert_ldt_to_ies(data, source_name=""):
    """Render an ``LDTPhotometry`` as an IESNA LM-63-2002 payload."""
    width, length, height = data.luminous_size_meters()

    num_lamps = max(1, data.num_lamps)
    if data.absolute_photometry or data.total_flux <= 0.0:
        lumens_per_lamp = -1.0
    else:
        lumens_per_lamp = data.total_flux / num_lamps

    lines = ["IESNA:LM-63-2002"]

    def keyword(tag, value):
        value = (value or "").strip()
        if value:
            lines.append("[%s] %s" % (tag, value[:200]))

    keyword("TEST", data.report_number)
    keyword("TESTLAB", data.company)
    keyword("MANUFAC", data.company)
    keyword("LUMINAIRE", data.luminaire_name)
    keyword("LUMCAT", data.luminaire_number)
    keyword("LAMP", data.lamp_type)
    # EULUMDAT stores the color appearance as a bare number, add the unit so
    # IES readers recognize it as a color temperature.
    color_appearance = (data.color_appearance or "").strip()
    if re.fullmatch(r"\d{3,5}", color_appearance):
        color_appearance += " K"
    keyword("COLORTEMPERATURE", color_appearance)
    keyword("CRI", data.color_rendering)
    keyword("ISSUEDATE", data.date_user)
    keyword("_CONVERTEDFROM", source_name or data.file_name or "EULUMDAT")
    keyword("_CONVERTER", "Photometric Light Plus")
    if data.light_output_ratio > 0.0:
        keyword("_LORL", "%.2f %%" % data.light_output_ratio)
    if data.downward_flux_fraction > 0.0:
        keyword("_DFF", "%.2f %%" % data.downward_flux_fraction)
    for warning in data.warnings:
        keyword("_WARNING", warning)

    lines.append("TILT=NONE")
    lines.append(
        "%d %s 1 %d %d 1 2 %s %s %s"
        % (
            num_lamps,
            _format_number(lumens_per_lamp),
            len(data.gamma_angles),
            len(data.c_angles),
            _format_number(width),
            _format_number(length),
            _format_number(height),
        )
    )
    lines.append("1 1 %s" % _format_number(data.wattage))
    lines.extend(_wrap_values(data.gamma_angles))
    lines.extend(_wrap_values(data.c_angles))
    for row in data.candela:
        lines.extend(_wrap_values(row))

    return "\n".join(lines) + "\n"


def ldt_string_to_ies(text, source_name=""):
    """Convenience wrapper: EULUMDAT text in, LM-63 text out."""
    return convert_ldt_to_ies(parse_ldt_string(text), source_name=source_name)


def total_flux_estimate(data):
    """Integrate the expanded distribution, useful for validation reports."""
    if len(data.gamma_angles) < 2:
        return 0.0
    total = 0.0
    c_count = len(data.c_angles)
    closed = c_count > 1 and abs(data.c_angles[-1] - 360.0) < 1e-6
    planes = c_count - 1 if closed else c_count
    d_phi = 2.0 * math.pi / max(1, planes)
    for c_index in range(planes):
        row = data.candela[c_index]
        for g_index in range(len(data.gamma_angles) - 1):
            g_lo = math.radians(data.gamma_angles[g_index])
            g_hi = math.radians(data.gamma_angles[g_index + 1])
            mean = 0.5 * (row[g_index] + row[g_index + 1])
            total += mean * (math.cos(g_lo) - math.cos(g_hi)) * d_phi
    return total
