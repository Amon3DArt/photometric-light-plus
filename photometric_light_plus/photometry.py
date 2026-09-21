# ------------------------------------------------------------------------------------------
#  Copyright (c) Marco Caturano. All rights reserved.
#  Licensed under the GPLv3 License. See LICENSE in the project root for license information.
# ------------------------------------------------------------------------------------------
"""Radiometric conversions and halo mesh generation.

No Blender data is touched here: the module only consumes an ``IESPhotometry``
instance and returns plain arrays, which keeps it safe to call from the draw
handler (read only, main thread, no dependency graph interaction).
"""

import math

import numpy as np

__all__ = (
    "MAX_LUMINOUS_EFFICACY",
    "RESOLUTION_PRESETS",
    "build_halo_geometry",
    "luminous_efficacy_of_radiation",
    "watts_from_lumens",
)

# Maximum possible luminous efficacy, at 555 nm. Blender expresses light power
# as radiometric watts and every photometric conversion inside Blender (sun
# strength in W/m2 read as lux, light power read as lumens) is built on this
# constant, so it is the correct divisor to keep an IES luminaire consistent
# with the rest of a Blender scene.
MAX_LUMINOUS_EFFICACY = 683.0

# Cycles multiplies every candela value of an IES file by 4*pi/177.83 (see
# intern/cycles/util/ies.cpp), 177.83 lm/W being the assumed luminous efficacy
# of a D65 spectrum, and the 4*pi turning W/sr into the lamp wattage Cycles
# expects. The light Power then multiplies that node output, and a point light
# of P watts radiates P/(4*pi) W/sr, so the rendered luminous intensity works
# out to exactly P * candela. A Power of 1 W therefore reproduces the file at
# its true absolute photometric values.
CYCLES_D65_EFFICACY = 177.83
CYCLES_CANDELA_TO_WATT = 4.0 * math.pi / CYCLES_D65_EFFICACY
ABSOLUTE_IES_WATTS = 1.0


def absolute_watts(multiplier=1.0):
    """Light power that reproduces the IES file at its measured candela."""
    return ABSOLUTE_IES_WATTS * max(0.0, multiplier)

# Luminous efficacy of radiation (lm/W of *radiant* power) for typical white
# light spectra. Only used by the optional spectral mode, which answers "how
# many watts would a real lamp of this color temperature radiate", a much
# smaller divisor that therefore yields a much larger wattage.
_LER_TABLE = (
    (1800.0, 180.0),
    (2200.0, 210.0),
    (2700.0, 250.0),
    (3000.0, 270.0),
    (3500.0, 290.0),
    (4000.0, 300.0),
    (4500.0, 310.0),
    (5000.0, 320.0),
    (5500.0, 325.0),
    (6000.0, 330.0),
    (6500.0, 335.0),
    (7000.0, 340.0),
    (8000.0, 345.0),
    (10000.0, 350.0),
)

# (theta segments, phi segments) for the halo tessellation.
RESOLUTION_PRESETS = {
    "LOW": (32, 48),
    "MEDIUM": (48, 72),
    "HIGH": (72, 120),
}


def luminous_efficacy_of_radiation(cct, fallback=3000.0):
    """Approximate LER in lm/W for a given correlated color temperature."""
    if cct is None or not math.isfinite(cct):
        cct = fallback
    cct = max(_LER_TABLE[0][0], min(float(cct), _LER_TABLE[-1][0]))
    for index in range(len(_LER_TABLE) - 1):
        cct_lo, ler_lo = _LER_TABLE[index]
        cct_hi, ler_hi = _LER_TABLE[index + 1]
        if cct <= cct_hi:
            span = cct_hi - cct_lo
            factor = 0.0 if span <= 0.0 else (cct - cct_lo) / span
            return ler_lo + (ler_hi - ler_lo) * factor
    return _LER_TABLE[-1][1]


def watts_from_lumens(lumens, efficacy=MAX_LUMINOUS_EFFICACY, multiplier=1.0):
    """Convert luminous flux into the radiant power used by ``Light.energy``.

    ``efficacy`` is in lm/W. The default matches Blender's own photometric
    convention; passing a spectral LER instead models the waste heat of a real
    lamp and produces a wattage roughly 2.3x higher for the same lumens.
    """
    if lumens is None or lumens <= 0.0:
        return 0.0
    if efficacy is None or efficacy <= 0.0:
        return 0.0
    return max(0.0, (lumens / efficacy) * max(0.0, multiplier))


def _sample_intensity_grid(photometry, thetas, phis):
    """Sample the normalized distribution on a (theta, phi) grid.

    Returns a ``(theta_count, phi_count)`` float32 array in the 0..1 range.
    """
    theta_count = thetas.size
    phi_count = phis.size

    sin_t = np.sin(thetas)
    cos_t = np.cos(thetas)
    sin_p = np.sin(phis)
    cos_p = np.cos(phis)

    # Direction convention: V = 0 -> -Z, H measured as atan2(x, y).
    dir_x = np.outer(sin_t, sin_p)
    dir_y = np.outer(sin_t, cos_p)
    dir_z = np.repeat(-cos_t[:, None], phi_count, axis=1)

    values = np.empty((theta_count, phi_count), dtype=np.float64)
    sample = photometry.candela_at_direction
    for ti in range(theta_count):
        row_x = dir_x[ti]
        row_y = dir_y[ti]
        row_z = dir_z[ti]
        out = values[ti]
        for pi_index in range(phi_count):
            out[pi_index] = sample(
                float(row_x[pi_index]),
                float(row_y[pi_index]),
                float(row_z[pi_index]),
            )

    peak = photometry.max_candela
    if peak <= 0.0:
        peak = float(values.max()) or 1.0
    np.divide(values, peak, out=values)
    np.clip(values, 0.0, 1.0, out=values)
    return values.astype(np.float32)


def build_halo_geometry(photometry, resolution="MEDIUM", gamma=1.0, min_radius=0.02):
    """Build the ghost shaped photometric solid.

    Returns ``(positions, normals, intensities, triangle_indices, line_indices)``
    with positions expressed in a unit sphere space: the caller scales them with
    the object matrix, so the same batch is reused for any halo size.
    """
    theta_segments, phi_segments = RESOLUTION_PRESETS.get(
        resolution, RESOLUTION_PRESETS["MEDIUM"]
    )

    theta_count = theta_segments + 1
    phi_count = phi_segments  # phi wraps around, no duplicated seam column

    thetas = np.linspace(0.0, math.pi, theta_count, dtype=np.float64)
    phis = np.linspace(
        0.0, 2.0 * math.pi, phi_count, endpoint=False, dtype=np.float64
    )

    intensity = _sample_intensity_grid(photometry, thetas, phis)

    gamma = max(0.05, float(gamma))
    radius = np.power(intensity.astype(np.float64), 1.0 / gamma)
    radius = np.maximum(radius, float(min_radius))

    sin_t = np.sin(thetas)[:, None]
    cos_t = np.cos(thetas)[:, None]
    sin_p = np.sin(phis)[None, :]
    cos_p = np.cos(phis)[None, :]

    unit_x = sin_t * sin_p
    unit_y = sin_t * cos_p
    unit_z = np.repeat(-cos_t, phi_count, axis=1)

    positions = np.empty((theta_count, phi_count, 3), dtype=np.float64)
    positions[..., 0] = unit_x * radius
    positions[..., 1] = unit_y * radius
    positions[..., 2] = unit_z * radius

    # Smooth normals from finite differences along both grid directions, with a
    # radial fallback wherever the surface degenerates (poles, zero intensity).
    d_theta = np.gradient(positions, axis=0)
    d_phi = (
        np.roll(positions, -1, axis=1) - np.roll(positions, 1, axis=1)
    ) * 0.5

    normals = np.cross(d_phi, d_theta)
    lengths = np.linalg.norm(normals, axis=2, keepdims=True)

    radial = np.empty_like(positions)
    radial[..., 0] = unit_x
    radial[..., 1] = unit_y
    radial[..., 2] = unit_z

    degenerate = lengths < 1e-9
    normals = np.where(degenerate, radial, normals / np.maximum(lengths, 1e-9))

    # Keep the normals pointing outwards so the rim term stays stable.
    facing = np.sum(normals * radial, axis=2, keepdims=True)
    normals = np.where(facing < 0.0, -normals, normals)

    flat_positions = positions.reshape(-1, 3).astype(np.float32)
    flat_normals = normals.reshape(-1, 3).astype(np.float32)
    flat_intensity = intensity.reshape(-1).astype(np.float32)

    # -- triangle indices ----------------------------------------------------
    row_indices = np.arange(theta_count - 1, dtype=np.int32)[:, None]
    col_indices = np.arange(phi_count, dtype=np.int32)[None, :]
    next_col = (col_indices + 1) % phi_count

    index_00 = row_indices * phi_count + col_indices
    index_01 = row_indices * phi_count + next_col
    index_10 = (row_indices + 1) * phi_count + col_indices
    index_11 = (row_indices + 1) * phi_count + next_col

    tris_a = np.stack((index_00, index_10, index_11), axis=-1)
    tris_b = np.stack((index_00, index_11, index_01), axis=-1)
    triangles = np.concatenate(
        (tris_a.reshape(-1, 3), tris_b.reshape(-1, 3)), axis=0
    ).astype(np.int32)

    # -- wireframe indices ---------------------------------------------------
    meridian_step = max(1, phi_count // 12)
    parallel_step = max(1, (theta_count - 1) // 8)

    lines = []
    for col in range(0, phi_count, meridian_step):
        base = np.arange(theta_count - 1, dtype=np.int32) * phi_count + col
        lines.append(np.stack((base, base + phi_count), axis=-1))
    for row in range(0, theta_count, parallel_step):
        base = row * phi_count + np.arange(phi_count, dtype=np.int32)
        nxt = row * phi_count + (np.arange(phi_count, dtype=np.int32) + 1) % phi_count
        lines.append(np.stack((base, nxt), axis=-1))

    line_indices = (
        np.concatenate(lines, axis=0).astype(np.int32)
        if lines
        else np.zeros((0, 2), dtype=np.int32)
    )

    return (
        flat_positions,
        flat_normals,
        flat_intensity,
        triangles,
        line_indices,
    )
