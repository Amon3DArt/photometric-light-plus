# Submission checklist

## 0. Fill in the placeholders

`photometric_light_plus/blender_manifest.toml` ships with two deliberate
placeholders so the build fails until they are set:

- `maintainer = "Marco Caturano <REPLACE_WITH_YOUR_EMAIL>"` — the address the
  moderation team writes to.
- `website = "https://github.com/REPLACE_WITH_YOUR_ACCOUNT/photometric-light-plus"`
  — the public repository or product page. If there is none yet, delete the
  whole line: `website` is optional, but an empty value is invalid.

`python3 tools/validate_manifest.py` reports both until they are replaced.

## 1. Check the package locally

```bash
python3 tools/validate_manifest.py
./tools/build.sh
```

`build.sh` runs the offline check, builds with
`blender --command extension build` and then validates the resulting zip with
`blender --command extension validate`. Without a Blender binary it falls back
to a plain zip, which is fine for testing but skips the authoritative
validation — run it with Blender before uploading.

The zip must have `blender_manifest.toml` at its **root**, not inside a
subfolder. `build.sh` guarantees this by building from inside the package
directory.

## 2. Test the built zip

Install `dist/photometric_light_plus-1.0.0.zip` through
Edit ▸ Preferences ▸ Add-ons ▸ ▾ ▸ Install from Disk, on a clean profile, and
check:

- [ ] The add-on enables with no console error.
- [ ] Add ▸ Light ▸ Photometric Light opens the file browser and creates a light.
- [ ] Dropping a `.ies` and a `.ldt` into the viewport both work.
- [ ] The halo draws, follows the light, and disappears when the light is deleted.
- [ ] Saving, closing and reopening the .blend keeps the halo and the photometry.
- [ ] Moving the .blend to another folder (or machine) still works.
- [ ] Disabling and re-enabling the add-on leaves no stale draw handler.
- [ ] Undo and redo after an import do not produce errors.
- [ ] Repeat on both Blender 4.5 LTS and the current 5.x.

## 3. Create the Blender ID account

The upload needs a Blender ID (<https://id.blender.org>). Same account used for
blender.org services.

## 4. Upload

1. Go to <https://extensions.blender.org> and choose to add a new extension.
2. Upload the zip. The form is prefilled from the manifest; the fields that
   remain are the description, the images and the support links.
3. Paste the description from `docs/extension-listing.md`.
4. Attach the screenshots listed in the same file, thumbnail first.
5. Submit for review.

## 5. Review

The extension is held until a moderator approves it. Common reasons for a
rejection, all of which apply to this add-on:

- The description does not mention everything the add-on does. The Terms of
  Service ask for "no surprises" — the description here states the file access,
  the Cycles-only distribution and the internal storage.
- A declared permission with no matching behaviour, or missing permission for
  behaviour that exists. This add-on declares `files` and nothing else, which
  matches: no network, no clipboard, no camera, no microphone.
- A license that is not GPL compatible, or a missing `LICENSE` file. Both are
  in place (GPL-3.0-or-later, full text shipped inside the package).
- Bundled third party assets without a compatible license. Do not ship
  manufacturer `.ies` files unless their redistribution is allowed; the
  add-on ships none.

Answer any moderator comment in the thread on the extension page; a new upload
is only needed if code changes.

## 5b. Rules learned from the review

The moderation team rejected the first submission over two points; both are
worth keeping in mind for every future version.

- **No tag that does not describe the add-on.** `Render` was removed: the
  add-on creates lights, it does not render.
- **No load handler that iterates over data-blocks and changes them.** Anything
  that runs on file open and rewrites the user's data is considered intrusive
  and will not be accepted, however useful the migration is. Report the
  situation in the UI and let the user trigger the fix with an operator.

The same reasoning extends to timers that poll and write, to `msgbus`
subscriptions that mutate data, and to anything that touches data-blocks the
add-on did not create.

## 6. Releasing an update

1. Bump `version` in the manifest, following semantic versioning.
2. Add the entry to `CHANGELOG.md`.
3. Rebuild, retest, upload the new zip as a new version on the same listing.

Older versions stay available, so a version that has been published is never
edited in place.
