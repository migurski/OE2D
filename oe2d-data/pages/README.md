# oe2d-data/pages — per-page (single-image) training set

Training data for the `oe2d.pages` single-image analyzer, which reports in-page
facts about one election-results page (candidate orientation, whether contest
names / candidate names / headers are visible, precinct scope and axis). Distinct
from the per-file categorizer set in `oe2d-data/labels/category.jsonl`.

Page skew is not a label here — a VLM can't estimate fine rotation, so skew is
detected deterministically in `oe2d.pages.deskew`, not learned.

## Contents
- `images/` — committed page PNGs, one per training example (75). Rendered from
  the fixtures in `oe2d-data/fixtures/categorize/`, density-tiered (dense
  candidate-column tables at 300 DPI, sparser candidate-row pages at 220) and run
  through `optipng`.
- `labels.jsonl` — one row per image:
  - `image` — path relative to this directory (`images/<name>.png`).
  - `source_fixture`, `fixture_page` — provenance: which fixture page this came
    from (fixture path is relative to `oe2d-data/labels/`, matching the other
    manifests).
  - `synthetic` — true for the `crop_top` header-absence negatives;
    **synthetic rows are TRAIN-ONLY**, validation is measured on real pages (see
    `oe2d.pages.datasets.split`).
  - `transform`, `params` — how a synthetic image was made (`crop_top` drops the
    top slice to simulate a header-less continuation page), null for real pages.
    The image is already materialized; these are provenance, not applied at load.
  - `role` — the page's window role from the fixture (`results` /
    `continuation-columns` / `continuation-rows`); metadata, not a predicted field.
  - the property fields: `candidate_orientation`, `contest_name_present`,
    `candidate_names_present`, `headers_present`, `precinct_scope`,
    `precinct_orientation`.

## Property semantics and how the labels were derived
See the git history and the categorization handoff (`fixture-notes.md`) — these
were compiled from the fixture windows, a visual review of every page, and
recorded rulings (e.g. `precinct_scope` distinguishes per-precinct blocks from
county aggregates; a null `precinct_orientation` is normalized to `none` at load).

## Regenerating
The images are derived from the fixtures. To rebuild them, render each fixture
page at its density-tiered DPI, apply the `transform` for synthetic rows, and run
`optipng`. Keep the split honest: never let a synthetic row into validation, and
split by `source_fixture` so pages from one fixture don't span train and val.
