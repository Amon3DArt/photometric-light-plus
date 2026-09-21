# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/).

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
