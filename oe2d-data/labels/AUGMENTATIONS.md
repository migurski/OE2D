# augmentations.jsonl — synthetic training examples for the single-image program

Two per-page properties can't be learned from the real fixtures alone (see
PAGE_PROPERTIES.md "Known gaps"):
- **skew** — vector renders are all exactly 0°, and only 8 scanned pages have any
  skew (unmeasured). No positive signal to learn a degrees-off-zero estimator.
- **headers_present / candidate_names_present** — header-omission on continuation
  pages turned out to be rare and vendor-specific (only allegan among reviewed
  vendors; the green-Clarity family and Electionware reprint headers every page),
  so the real set has just 2 negatives.

Both are closed the same principled way: apply a deterministic transform to a
real page render, which produces a new image with an EXACTLY known label. Each
row is a recipe the loader executes at train time (no image files committed):

    { base_path, base_fixture_page, transform, params, labels }

- `base_path` / `base_fixture_page` — the real page to render first (relative to
  the labels dir, same convention as the other JSONLs). All bases are vector
  results pages (crisp, skew-0, full headers).
- `transform` + `params`:
  - `rotate` — `{degrees, expand:true, fill:"white"}`: rotate the render by a
    known angle (positive = counter-clockwise, PIL convention). Simulates scanner
    skew. `labels.skew_degrees` = the angle; all other labels inherit the base.
  - `crop_top` — `{remove_fraction:0.22}`: drop the top slice (title + header
    band), leaving bare data rows — a faithful stand-in for a mid-table
    continuation page. `labels` set `headers_present`, `candidate_names_present`,
    `contest_name_present` = false.
- `labels` — the full per-page label dict for the transformed image (same schema
  as PAGE_PROPERTIES.md), base values with the transform's overrides applied.

## Contents
- 80 `rotate` rows: 10 diverse vector vendors × angles
  {-3,-2,-1,-0.5,0.5,1,2,3}°. Train a skew regressor here; validate on the 8 real
  scanned pages once their angles are measured.
- 15 `crop_top` rows: 15 diverse vector results pages → bare-data negatives for
  headers/candidate-names, complementing the 2 real allegan negatives.

## Caveats
- These are SYNTHETIC. Keep them out of the validation/test split — validate on
  real pages so scores reflect real performance. rotate covers ±3° only; real
  scans can be worse (and add non-skew noise: hole-punches, contrast, handwriting
  — see fixture-notes.md). crop_top approximates one failure mode of
  header-absence, not all of them.
