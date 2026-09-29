# Extensions Platform listing copy

Everything below is meant to be pasted into the form at
<https://extensions.blender.org>. Fields that Blender reads from
`blender_manifest.toml` are listed first so they can be cross checked.

---

## Read from the manifest (do not retype)

| Field | Value |
| --- | --- |
| Name | Photometric Light Plus |
| ID | `photometric_light_plus` |
| Version | 1.0.1 |
| Tagline | IES and EULUMDAT lights with a viewport photometric halo |
| Type | add-on |
| Tags | Lighting, Import-Export |
| Blender version | 4.5.0 and above |
| License | GPL-3.0-or-later |
| Copyright | 2026 Marco Caturano |
| Permissions | files — Read IES and LDT photometric files chosen by the user |

---

## Description (Markdown, paste into the listing)

Photometric Light Plus imports real luminaire data and turns it into a Blender
light you can actually judge in the viewport.

### What it does

**Reads IES and EULUMDAT.** Both `.ies` (IESNA LM-63, 1986 to 2002) and `.ldt`
(EULUMDAT) files are supported. EULUMDAT files are converted to LM-63 on
import, including every symmetry case, so the scene only ever holds one
format.

**Drag and drop.** Drop a photometric file into the 3D viewport and the
luminaire is created where you dropped it: the placement ray casts against the
visible geometry, so a light dropped on a ceiling stays on the ceiling.

**Shows the light distribution.** Instead of building a photometric web mesh
that has to be parented, updated and deleted along with the light, the
distribution is drawn as a translucent GPU overlay, the same way Blender draws
the spot cone. It reacts to the light color, it never shows up in a render, it
never appears in the outliner, and it disappears the moment the light is
deleted. Size, opacity, rim falloff, shape gamma, tessellation density and an
optional wireframe are all adjustable per light, and the overlay can be limited
to selected lights or switched off scene wide.

**Keeps the data inside the .blend.** The photometric payload is copied into an
internal text data-block and the IES texture node is forced to internal mode.
Move the .blend to another machine, hand it to a colleague, render it on a
farm: nothing points at a file path that no longer exists. A "Pack All IES
Data" operator converts lights that still reference external files.

**Gets the photometry right.** The parser follows LM-63 strictly, including the
ballast record that many readers skip, `TILT=INCLUDE` blocks, feet or meters
geometry, candela multipliers and photometric types A, B and C. The post
processing stage is a port of Cycles' own conversion code, so the preview
matches what Cycles renders instead of merely resembling it. On a typical
manufacturer file the integrated luminous flux reproduces the lumen figure
printed in the file header.

**Derives the light power physically.** By default the light power is set so
the render reproduces the absolute candela values stored in the file.
Lumen based modes are available as well, with a choice of luminous efficacy,
plus a plain multiplier for artistic control.

**Stays out of the way.** The add-on registers no application handler: it never runs code when a file is opened, never walks over data-blocks it did not create and never modifies anything without being asked. A light whose IES data still lives outside the .blend is reported in its panel, with the packing operator one click away.

**Reports what it read.** One click prints a full photometric report to the
console: photometric type, angle grid, symmetry, peak intensity, beam angle,
luminous dimensions, declared lumens against integrated lumens, and any
irregularity found in the file.

### Requirements

The IES distribution itself is evaluated by **Cycles**. EEVEE ignores light
node trees, so with EEVEE you still get the viewport halo and the correct
power, but not the distribution in the render. The add-on shows a note when the
active engine cannot render IES.

### Usage

- Add ▸ Light ▸ Photometric Light (.ies / .ldt), or drop a file into the
  viewport.
- Per light settings: Light Data Properties ▸ Photometric Light.
- Scene wide controls: 3D viewport sidebar (N) ▸ Tool ▸ Photometric Lights.

---

## Permission explanation (if the reviewer asks)

The add-on reads a photometric file only when the user picks one through the
file browser or drops one into the viewport. It reads the file once, copies the
content into the .blend and never touches the path again. It never writes to
disk, never opens a network connection and never runs anything outside the
add-on directory.

---

## Screenshots to attach

The listing accepts several images; the first one becomes the thumbnail.
Suggested set, 1920 × 1080 PNG, no UI theme customisation:

1. **The halo in context.** A luminaire in an interior, light selected, halo
   visible next to the lit surface. This is the thumbnail: it has to show at a
   glance what the add-on adds to the viewport.
2. **The light data panel.** Photometric Light and Photometric Halo panels
   expanded, showing the info line (type, angle grid, peak candela, beam
   angle), the power mode and the halo controls.
3. **Drag and drop.** A file being dropped into the viewport, or the resulting
   light immediately after the drop.
4. **Several luminaires side by side.** Different distributions (downlight,
   wall washer, uplight) with their halos, showing that the preview reflects
   the actual file.
5. **Optional: the console report**, cropped to the report block.

Keep the theme default, hide the overlays you do not need for the shot, and
avoid showing unreleased or third party luminaire geometry you cannot
redistribute.
