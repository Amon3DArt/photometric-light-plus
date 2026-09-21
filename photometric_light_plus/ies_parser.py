# ------------------------------------------------------------------------------------------
#  Copyright (c) Marco Caturano. All rights reserved.
#  Licensed under the GPLv3 License. See LICENSE in the project root for license information.
# ------------------------------------------------------------------------------------------
"""Strict IESNA LM-63 parser (1986 / 1991 / 1995 / 2002).

Pure python, no Blender dependency, so it can be unit tested outside of Blender.

The post processing stage is a faithful port of Cycles ``IESFile::process()``
(``intern/cycles/util/ies.cpp``): photometric types A and B are converted to
type C exactly the way Cycles converts them, and type C tables are mirrored to
the full 0 to 360 degree range. That is what makes the viewport halo match the
render instead of merely resembling it.

Direction convention, in light object local space:

* the vertical angle is ``V = acos(-dir.z)``, so V = 0 (the IES nadir) points
  towards **-Z**, straight down for an unrotated light,
* the horizontal angle is ``H = atan2(dir.x, dir.y) + 180``.

This is verified against how Cycles renders the light: a file whose vertical
angles only span 0 to 90 degrees lights the lower hemisphere, so the halo must
occupy that same hemisphere.
"""

import math
import re

__all__ = (
    "IESParseError",
    "IESPhotometry",
    "parse_ies_string",
)

# Values may be separated by whitespace and/or commas, and a logical record may
# be wrapped over several physical lines.
_TOKEN_SPLIT = re.compile(r"[,\s]+")
_KEYWORD_RE = re.compile(r"^\[([^\]]+)\]\s*(.*)$")

FEET_TO_METERS = 0.3048

# Photometric type identifiers as stored in the IES file.
PHOTOMETRIC_TYPE_C = 1
PHOTOMETRIC_TYPE_B = 2
PHOTOMETRIC_TYPE_A = 3

_ANGLE_EPSILON = 1e-4


class IESParseError(Exception):
    """Raised when the IES payload cannot be interpreted."""


def _angle_close(a, b):
    return abs(a - b) < _ANGLE_EPSILON


class _AxisLookup:
    """1D lookup table over a monotonically increasing angle list."""

    __slots__ = ("angles", "count", "first", "last")

    def __init__(self, angles):
        self.angles = angles
        self.count = len(angles)
        self.first = angles[0]
        self.last = angles[-1]

    def locate(self, value):
        """Return ``(index_lo, index_hi, weight_hi)`` for a clamped value."""
        angles = self.angles
        if self.count == 1:
            return 0, 0, 0.0
        if value <= self.first:
            return 0, 0, 0.0
        if value >= self.last:
            last = self.count - 1
            return last, last, 0.0

        # Binary search: IES angle lists are short but can be non uniform.
        low = 0
        high = self.count - 1
        while high - low > 1:
            mid = (low + high) // 2
            if angles[mid] <= value:
                low = mid
            else:
                high = mid
        span = angles[high] - angles[low]
        if span <= 0.0:
            return low, high, 0.0
        return low, high, (value - angles[low]) / span


class IESPhotometry:
    """Parsed photometric data plus direction based sampling helpers."""

    __slots__ = (
        "keywords",
        "num_lamps",
        "lumens_per_lamp",
        "multiplier",
        "photometric_type",
        "original_type",
        "units_type",
        "width",
        "length",
        "height",
        "ballast_factor",
        "ballast_lamp_factor",
        "input_watts",
        "v_angles",
        "h_angles",
        "candela",
        "max_candela",
        "tilt",
        "warnings",
        "_v_lookup",
        "_h_lookup",
    )

    def __init__(self):
        self.keywords = {}
        self.num_lamps = 1
        self.lumens_per_lamp = -1.0
        self.multiplier = 1.0
        self.photometric_type = PHOTOMETRIC_TYPE_C
        self.original_type = PHOTOMETRIC_TYPE_C
        self.units_type = 2
        self.width = 0.0
        self.length = 0.0
        self.height = 0.0
        self.ballast_factor = 1.0
        self.ballast_lamp_factor = 1.0
        self.input_watts = 0.0
        self.v_angles = []
        self.h_angles = []
        # candela[h_index][v_index], already scaled by every multiplier.
        self.candela = []
        self.max_candela = 0.0
        self.tilt = "NONE"
        self.warnings = []
        self._v_lookup = None
        self._h_lookup = None

    # -- Cycles compatible post processing -----------------------------------

    def _process_type_b(self):
        """Port of ``IESFile::process_type_b``.

        Type B stores a horizontal polar axis. Cycles simply transposes the
        table and reuses the type A/C coordinate system, so the halo has to do
        exactly the same, orientation quirk included.
        """
        transposed = [
            [self.candela[h][v] for h in range(len(self.h_angles))]
            for v in range(len(self.v_angles))
        ]
        self.candela = transposed
        self.h_angles, self.v_angles = self.v_angles, self.h_angles

        if _angle_close(self.h_angles[0], 0.0):
            # 0..90 in the file: mirror to -90..90, then shift to 0..180.
            count = len(self.h_angles)
            new_angles = [90.0 - self.h_angles[i] for i in range(count - 1, 0, -1)]
            new_rows = [self.candela[i] for i in range(count - 1, 0, -1)]
            new_angles += [90.0 + angle for angle in self.h_angles]
            new_rows += list(self.candela)
            self.h_angles = new_angles
            self.candela = new_rows
        else:
            self.h_angles = [angle + 90.0 for angle in self.h_angles]

        if _angle_close(self.v_angles[0], 0.0):
            count = len(self.v_angles)
            new_angles = [90.0 - self.v_angles[i] for i in range(count - 1, 0, -1)]
            new_angles += [90.0 + angle for angle in self.v_angles]
            self.candela = [
                [row[i] for i in range(count - 1, 0, -1)] + list(row)
                for row in self.candela
            ]
            self.v_angles = new_angles
        else:
            self.v_angles = [angle + 90.0 for angle in self.v_angles]

    def _process_type_a(self):
        """Port of ``IESFile::process_type_a``."""
        self.v_angles = [angle + 90.0 for angle in self.v_angles]

        count = len(self.h_angles)
        # Type A runs -90..90, which maps to 270..90 in type C.
        new_angles = [180.0 - self.h_angles[i] for i in range(count - 1, -1, -1)]
        new_rows = [self.candela[i] for i in range(count - 1, -1, -1)]

        if _angle_close(self.h_angles[0], 0.0):
            # The generated negative range maps to 180..270, after the rest.
            new_angles += [180.0 + self.h_angles[i] for i in range(1, count)]
            new_rows += [self.candela[i] for i in range(1, count)]

        self.h_angles = new_angles
        self.candela = new_rows

    def _process_type_c(self):
        """Port of ``IESFile::process_type_c``."""
        if _angle_close(self.h_angles[0], 90.0):
            # Some files are stored from 90 to 270, rotate back to 0..180.
            self.h_angles = [angle - 90.0 for angle in self.h_angles]

        if len(self.h_angles) == 1:
            self.h_angles = [0.0, 360.0]
            self.candela = [self.candela[0], list(self.candela[0])]

        if _angle_close(self.h_angles[-1], 90.0):
            # One quadrant only: mirror once here, the half to full mirroring
            # below then completes the sphere.
            count = len(self.h_angles)
            for index in range(count - 2, -1, -1):
                self.h_angles.append(180.0 - self.h_angles[index])
                self.candela.append(self.candela[index])

        if _angle_close(self.h_angles[-1], 180.0):
            count = len(self.h_angles)
            for index in range(count - 2, -1, -1):
                self.h_angles.append(360.0 - self.h_angles[index])
                self.candela.append(self.candela[index])

        # Some files skip the 360 entry although it repeats the 0 entry. Add it
        # back when the angle spacing makes the intent obvious.
        if _angle_close(self.h_angles[0], 0.0) and not _angle_close(
            self.h_angles[-1], 360.0
        ):
            count = len(self.h_angles)
            if count > 1:
                last_step = self.h_angles[-1] - self.h_angles[-2]
                first_step = self.h_angles[1] - self.h_angles[0]
                gap_step = 360.0 - self.h_angles[-1]
                if _angle_close(last_step, gap_step) or _angle_close(
                    first_step, gap_step
                ):
                    self.h_angles.append(360.0)
                    self.candela.append(self.candela[0])

    def _process(self):
        """Turn any photometric type into the type C table Cycles works with."""
        self.original_type = self.photometric_type

        if self.photometric_type == PHOTOMETRIC_TYPE_A:
            self._process_type_a()
        elif self.photometric_type == PHOTOMETRIC_TYPE_B:
            self._process_type_b()
        else:
            self._process_type_c()

        self.photometric_type = PHOTOMETRIC_TYPE_C

        # The mirroring steps can leave the angle lists unsorted on malformed
        # files, and the lookup requires them increasing.
        if any(
            self.h_angles[i] > self.h_angles[i + 1]
            for i in range(len(self.h_angles) - 1)
        ):
            order = sorted(range(len(self.h_angles)), key=lambda i: self.h_angles[i])
            self.h_angles = [self.h_angles[i] for i in order]
            self.candela = [self.candela[i] for i in order]
            self.warnings.append("Horizontal angles were out of order, sorted")

        if any(
            self.v_angles[i] > self.v_angles[i + 1]
            for i in range(len(self.v_angles) - 1)
        ):
            order = sorted(range(len(self.v_angles)), key=lambda i: self.v_angles[i])
            self.v_angles = [self.v_angles[i] for i in order]
            self.candela = [[row[i] for i in order] for row in self.candela]
            self.warnings.append("Vertical angles were out of order, sorted")

        self._finalize()

    def _finalize(self):
        self._v_lookup = _AxisLookup(self.v_angles)
        self._h_lookup = _AxisLookup(self.h_angles)

        self.max_candela = 0.0
        for row in self.candela:
            for value in row:
                if value > self.max_candela:
                    self.max_candela = value

    # -- sampling ------------------------------------------------------------

    def candela_at_angles(self, v, h):
        """Bilinear sample of the processed type C table (degrees)."""
        v_lookup = self._v_lookup
        # Nothing is emitted outside of the measured vertical range.
        if v < v_lookup.first - _ANGLE_EPSILON or v > v_lookup.last + _ANGLE_EPSILON:
            return 0.0

        h = h % 360.0
        h_lookup = self._h_lookup
        if h_lookup.count > 1 and h < h_lookup.first:
            # Tables that start above 0 wrap around the far side.
            if h + 360.0 <= h_lookup.last:
                h += 360.0

        v_lo, v_hi, v_w = v_lookup.locate(v)
        h_lo, h_hi, h_w = h_lookup.locate(h)

        row_lo = self.candela[h_lo]
        value = row_lo[v_lo] * (1.0 - v_w) + row_lo[v_hi] * v_w
        if h_hi != h_lo:
            row_hi = self.candela[h_hi]
            value_hi = row_hi[v_lo] * (1.0 - v_w) + row_hi[v_hi] * v_w
            value = value * (1.0 - h_w) + value_hi * h_w
        return value

    def angles_from_direction(self, x, y, z):
        """Convert a unit direction in light space to the Cycles angle pair."""
        v = math.degrees(math.acos(max(-1.0, min(1.0, -z))))
        h = (math.degrees(math.atan2(x, y)) + 180.0) % 360.0
        return v, h

    def direction_from_angles(self, v_deg, h_deg):
        """Inverse of ``angles_from_direction``, for geometry generation."""
        v = math.radians(v_deg)
        h = math.radians(h_deg) - math.pi
        sin_v = math.sin(v)
        return sin_v * math.sin(h), sin_v * math.cos(h), -math.cos(v)

    def candela_at_direction(self, x, y, z):
        v, h = self.angles_from_direction(x, y, z)
        return self.candela_at_angles(v, h)

    # -- derived quantities --------------------------------------------------

    def luminous_flux(self, theta_steps=180, phi_steps=72):
        """Integrate the distribution over the sphere, in lumens."""
        d_theta = math.pi / theta_steps
        d_phi = 2.0 * math.pi / phi_steps
        total = 0.0
        for ti in range(theta_steps):
            theta = (ti + 0.5) * d_theta
            sin_t = math.sin(theta)
            cos_t = math.cos(theta)
            ring = 0.0
            for pi_index in range(phi_steps):
                phi = (pi_index + 0.5) * d_phi
                ring += self.candela_at_direction(
                    sin_t * math.sin(phi), sin_t * math.cos(phi), -cos_t
                )
            total += ring * sin_t
        return total * d_theta * d_phi

    def beam_angle(self, fraction=0.5):
        """Full beam angle (degrees) where intensity drops below ``fraction``."""
        if self.max_candela <= 0.0:
            return 0.0
        threshold = self.max_candela * fraction
        limit = 0.0
        angle = 0.0
        step = 0.5
        while angle <= 180.0:
            peak = 0.0
            for h in self.h_angles:
                value = self.candela_at_angles(angle, h)
                if value > peak:
                    peak = value
            if peak >= threshold:
                limit = angle
            angle += step
        return limit * 2.0

    def luminous_size(self):
        """Luminous opening dimensions in meters ``(width, length, height)``."""
        scale = FEET_TO_METERS if self.units_type == 1 else 1.0
        return (
            abs(self.width) * scale,
            abs(self.length) * scale,
            abs(self.height) * scale,
        )

    def color_temperature(self):
        """Best effort CCT extraction from the keyword block."""
        candidates = (
            "COLORTEMPERATURE",
            "COLOR_TEMPERATURE",
            "COLORTEMP",
            "CCT",
            "LAMPCOLOR",
            "LAMP",
            "LUMCAT",
            "TEST",
        )
        for key in candidates:
            value = self.keywords.get(key)
            if not value:
                continue
            match = re.search(r"(\d{3,5})\s*K\b", value, re.IGNORECASE)
            if match:
                cct = float(match.group(1))
                if 1000.0 <= cct <= 20000.0:
                    return cct
        for value in self.keywords.values():
            match = re.search(r"\b(\d{4})\s*K\b", value, re.IGNORECASE)
            if match:
                cct = float(match.group(1))
                if 1000.0 <= cct <= 20000.0:
                    return cct
        return None

    def summary(self):
        width, length, height = self.luminous_size()
        names = {1: "C", 2: "B", 3: "A"}
        return {
            "photometric_type": names.get(self.original_type, "?"),
            "v_angles": len(self.v_angles),
            "h_angles": len(self.h_angles),
            "v_range": (self.v_angles[0], self.v_angles[-1]),
            "h_range": (self.h_angles[0], self.h_angles[-1]),
            "max_candela": self.max_candela,
            "declared_lumens": self.lumens_per_lamp * max(1, self.num_lamps),
            "input_watts": self.input_watts,
            "size": (width, length, height),
            "tilt": self.tilt,
            "warnings": tuple(self.warnings),
        }


def parse_ies_string(text):
    """Parse an IES payload held in memory and return an ``IESPhotometry``."""
    if not text:
        raise IESParseError("Empty IES data")

    lines = [line.strip() for line in text.splitlines()]

    photometry = IESPhotometry()

    tilt_index = None
    for index, line in enumerate(lines):
        if line.upper().startswith("TILT"):
            tilt_index = index
            break
        match = _KEYWORD_RE.match(line)
        if match:
            key = match.group(1).strip().upper().replace(" ", "")
            photometry.keywords[key] = match.group(2).strip()

    if tilt_index is None:
        raise IESParseError("Invalid IES data: the TILT record is missing")

    tilt_line = lines[tilt_index]
    photometry.tilt = (
        tilt_line.split("=", 1)[1].strip().upper() if "=" in tilt_line else "NONE"
    )

    tokens = []
    for line in lines[tilt_index + 1:]:
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
                return float(token)
            except ValueError:
                # Some exporters inject stray text inside the numeric block.
                photometry.warnings.append(
                    "Ignored non numeric token %r before %s" % (token, what)
                )
                continue
        raise IESParseError("Truncated IES data while reading %s" % what)

    # TILT=INCLUDE embeds its own block before the photometric records.
    if photometry.tilt == "INCLUDE":
        next_float("tilt geometry")
        tilt_pairs = int(next_float("tilt pair count"))
        for _ in range(max(0, tilt_pairs) * 2):
            next_float("tilt pair")

    photometry.num_lamps = max(1, int(next_float("number of lamps")))
    photometry.lumens_per_lamp = next_float("lumens per lamp")
    photometry.multiplier = next_float("candela multiplier")
    num_v = int(next_float("number of vertical angles"))
    num_h = int(next_float("number of horizontal angles"))
    photometry.photometric_type = int(next_float("photometric type"))
    photometry.units_type = int(next_float("units type"))
    photometry.width = next_float("luminous width")
    photometry.length = next_float("luminous length")
    photometry.height = next_float("luminous height")

    # Second record of the standard. Skipping it shifts the whole vertical
    # angle list by three entries, which is the classic IES reader bug.
    photometry.ballast_factor = next_float("ballast factor")
    photometry.ballast_lamp_factor = next_float("ballast lamp photometric factor")
    photometry.input_watts = next_float("input watts")

    if num_v < 1 or num_h < 1:
        raise IESParseError("Invalid angle counts (%d x %d)" % (num_v, num_h))
    if num_v * num_h > 4_000_000:
        raise IESParseError("Unreasonable angle counts (%d x %d)" % (num_v, num_h))

    v_angles = [next_float("vertical angle") for _ in range(num_v)]
    h_angles = [next_float("horizontal angle") for _ in range(num_h)]

    factor = photometry.multiplier
    if factor <= 0.0:
        photometry.warnings.append("Candela multiplier <= 0, forced to 1.0")
        factor = 1.0
    if photometry.ballast_factor > 0.0:
        factor *= photometry.ballast_factor
    if photometry.ballast_lamp_factor > 0.0:
        factor *= photometry.ballast_lamp_factor

    # Candela values are stored one full vertical block per horizontal angle.
    candela = []
    for _ in range(num_h):
        row = [next_float("candela value") * factor for _ in range(num_v)]
        candela.append(row)

    if position < token_count:
        photometry.warnings.append(
            "%d trailing token(s) ignored after the candela block"
            % (token_count - position)
        )

    # Angle lists must increase; some files ship them in descending order.
    if num_v > 1 and v_angles[0] > v_angles[-1]:
        v_angles.reverse()
        candela = [list(reversed(row)) for row in candela]
    if num_h > 1 and h_angles[0] > h_angles[-1]:
        h_angles.reverse()
        candela.reverse()

    if photometry.photometric_type not in (
        PHOTOMETRIC_TYPE_A,
        PHOTOMETRIC_TYPE_B,
        PHOTOMETRIC_TYPE_C,
    ):
        photometry.warnings.append(
            "Unknown photometric type %d, assuming type C"
            % photometry.photometric_type
        )
        photometry.photometric_type = PHOTOMETRIC_TYPE_C

    photometry.v_angles = v_angles
    photometry.h_angles = h_angles
    photometry.candela = candela
    photometry._process()

    if photometry.max_candela <= 0.0:
        raise IESParseError("The IES file contains no positive candela value")

    return photometry
