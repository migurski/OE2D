# oe2d-data/pages — per-page (single-image) training set

Training data for the `oe2d.pages` single-image analyzer, which reports in-page
facts about one election-results page (candidate orientation, whether contest
names / candidate names / headers are visible, precinct scope and axis, skew).
Distinct from the per-file categorizer set in `oe2d-data/labels/category.jsonl`.

## Contents
- `images/` — committed page PNGs, one per training example. Rendered from the
  fixtures in `oe2d-data/fixtures/categorize/`, density-tiered (dense
  candidate-column tables at 300 DPI, sparser candidate-row pages at 220) and run
  through `optipng`. Both real pages and the synthetic augmentations are here as
  real files.
- `labels.jsonl` — one row per image:
  - `image` — path relative to this directory (`images/<name>.png`).
  - `source_fixture`, `fixture_page` — provenance: which fixture page this came
    from (fixture path is relative to `oe2d-data/labels/`, matching the other
    manifests).
  - `synthetic` — true for the augmentations; **synthetic rows are TRAIN-ONLY**,
    validation is measured on real pages (see `oe2d.pages.datasets.split`).
  - `transform`, `params` — how a synthetic image was made (`rotate` for skew,
    `crop_top` for header-absence), null for real pages. The image is already
    materialized; these are provenance, not applied at load time.
  - `role` — the page's window role from the fixture (`results` /
    `continuation-columns` / `continuation-rows`); metadata, not a predicted field.
  - the property fields: `candidate_orientation`, `contest_name_present`,
    `candidate_names_present`, `headers_present`, `precinct_scope`,
    `precinct_orientation`, `skew_degrees`.

## Property semantics and how the labels were derived
See the git history and the categorization handoff (`fixture-notes.md`) — these
were compiled from the fixture windows, a visual review of every page, and
recorded rulings (e.g. `precinct_scope` distinguishes per-precinct blocks from
county aggregates; `skew_degrees` is 0.0 for vector renders and null/unmeasured
for real scans; synthetic `rotate` rows carry an exact known angle).

## Regenerating
The images are derived from the fixtures. To rebuild them, render each fixture
page at its density-tiered DPI, apply the `transform` for synthetic rows, and run
`optipng`. Keep the split honest: never let a synthetic row into validation, and
split by `source_fixture` so pages from one fixture don't span train and val.
