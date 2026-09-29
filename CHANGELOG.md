# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/).

## [1.0.1] - 2026-09-29

Changes requested during the Extensions Platform review, plus the follow-up
they implied.

### Removed

- The `Render` tag.
- Every application handler. The add-on no longer registers anything in
  `bpy.app.handlers`, so opening a .blend costs nothing whatever it contains.
  The automatic migration of lights that ran on file load is gone with it: the
  add-on no longer walks over data-blocks it did not create, and no longer
  modifies user data without being asked.

### Added

- The light data panel reports, read only, when a light still depends on an IES
  file stored outside the .blend, with the packing operator one click away.
- `Pack All IES Data` gained an `Active Light Only` scope, so adopting a single
  light is an explicit, local action.

### Changed

- The draw callback returns immediately when the file holds no photometric
  light, instead of walking the view layer objects on every redraw.
- Both caches are documented as keyed on `ID.session_uid`, which Blender never
  reuses within a session. Entries from a closed file can never be matched by
  mistake, which is what makes the load handlers unnecessary rather than merely
  optional.

## [1.0.0] - 2026-09-21

First public release. The prototype iterations that preceded it were internal
and are not published.

### Added

- Import of IESNA LM-63 (`.ies`) photometric files, supporting the 1986, 1991,
  1995 and 2002 revisions, `TILT=INCLUDE` blocks, photometric types A, B and C,
  feet or meters geometry, candela multipliers and the ballast record.
- Import of EULUMDAT (`.ldt`, `.eul`, `.ltd`) files, converted to LM-63 at
  import time, with all five symmetry indicators expanded over 360 degrees.
- Drag and drop of photometric files into the 3D viewport, with placement ray
  cast against the visible geometry.
- Viewport halo drawn as a GPU overlay instead of a scene mesh, with per light
  visibility mode, size, opacity, rim falloff, core gain, shape gamma,
  tessellation density, wireframe and color source.
- Internal storage of the payload in a text data-block, with the IES texture
  node forced to internal mode, plus a "Pack All IES Data" operator to migrate
  lights that still reference external files.
- Power derived from the photometry: absolute candela by default, integrated or
  declared lumens as alternatives, with selectable luminous efficacy.
- Photometric validation report printed to the console.

### Notes

- The post processing of the parsed data is a port of Cycles'
  `IESFile::process()`, so the viewport halo matches the Cycles render for
  every photometric type and symmetry case.
- The IES distribution is only evaluated by Cycles; EEVEE ignores light node
  trees and the add-on says so in the panel.
