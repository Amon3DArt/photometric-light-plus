# Photometric Light Plus

Blender extension that turns IES and EULUMDAT photometric files into calibrated
lights and previews their light distribution directly in the viewport.

Compatible with Blender 4.5 LTS and 5.x.

## Features

- **Import IES (.ies) and EULUMDAT (.ldt) files.** EULUMDAT is converted to
  IESNA LM-63 on import, so only one format ever reaches the .blend.
- **Drag and drop.** Drop a photometric file into the 3D viewport and the light
  is created under the pointer, ray cast onto the geometry below it.
- **Viewport halo.** The photometric solid is drawn as a translucent GPU
  overlay, the way Blender draws the spot cone: no mesh, no object, nothing to
  delete or keep in sync. Deleting the light removes the halo with it.
- **Fully self contained.** The payload is stored in an internal text
  data-block and the IES texture node is forced to internal mode, so a .blend
  keeps working after being moved to another machine.
- **Correct photometry.** The parser is a strict LM-63 reader whose post
  processing is a port of Cycles' own `IESFile::process()`, including the
  type A and type B conversions and the type C symmetry expansion, so the halo
  matches what Cycles renders rather than merely resembling it.
- **Physically based power.** Light power is derived from the absolute candela
  values, with optional lumen based modes and a selectable luminous efficacy.
- **Validation report.** A one-click report prints the parsed photometry, the
  declared lumens and the integrated luminous flux to the console.

## Installation

From the Blender Extensions Platform, or manually:

1. Download the `.zip` from the releases page.
2. Edit ▸ Preferences ▸ Add-ons ▸ ▾ ▸ Install from Disk.

## Usage

- Add ▸ Light ▸ Photometric Light (.ies / .ldt), or drop a file into the
  viewport.
- Settings live in Light Data Properties ▸ Photometric Light, and a summary
  sits in the 3D viewport sidebar (N) under the Tool tab.

IES data is only evaluated by Cycles: EEVEE ignores light node trees, so the
distribution shows in the viewport halo but not in an EEVEE render.

## Building

```bash
./tools/build.sh                     # uses the blender found in PATH
BLENDER=/path/to/blender ./tools/build.sh
```

The script runs the offline manifest check, builds the package with
`blender --command extension build` and validates the resulting zip.

## Repository layout

```
photometric_light_plus/   the extension package (manifest at its root)
  __init__.py             registration and handlers
  ies_parser.py           LM-63 parser, Cycles compatible post processing
  ldt_parser.py           EULUMDAT reader and LM-63 converter
  photometry.py           flux, power conversion, halo geometry
  storage.py              internal payload storage and parse cache
  energy.py               light construction, node graph, power
  overlay.py              GPU halo draw handler
  properties.py           per light settings
  operators.py            import, replace, reload, pack, validate
  ui.py                   panels
docs/                     listing copy and submission checklist
tools/                    build and manifest validation scripts
```

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
