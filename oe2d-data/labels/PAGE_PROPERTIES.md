# page_properties.jsonl — per-page (single-image) labels

One row per rendered page of the re-excerpted fixtures (59 pages across 24
fixtures). This is the training set for a **single-image** DSPy program that
reports in-page facts to guide extraction (e.g. hint the pdfplumber table
finder) — distinct from the per-*file* source categorizer (`category.jsonl`) and
from inter-page / whole-document stitching, which live at other levels.

Join to an image by rendering `path` page `fixture_page` (1-based). `path` is
relative to THIS file's directory (`oe2d-data/labels/`), e.g.
`../fixtures/categorize/<name>.pdf`; `segments.jsonl` uses the same convention.
`source_page` is the page number in the upstream original (see `segments.jsonl`).

## Fields
- `path`, `fixture_page`, `source_page`, `role` — identity + window role
  (`results` / `continuation-columns` / `continuation-rows`).
- `orientation` — the fixture's overall candidate orientation (from category.jsonl).
- `candidate_orientation` — candidates on THIS page in `columns` or `rows`.
- `contest_name_present` — is a contest title visible on this page?
- `candidate_names_present` — are candidate/party names visible on this page?
- `headers_present` — are column/row headers (labeling the numbers) present?
- `precincts_present` — is there a precinct AXIS on the page?
- `precinct_orientation` — `rows` where present, else null.
- `skew_degrees` — `0.0` for vector renders (exact: vector PDFs rasterize with no
  skew). `null` for scanned pages (not yet measured — deliberately not fabricated;
  the qualitative scan condition is in `fixture-notes.md`). So a number means a
  known/exact angle and `null` means unmeasured; no separate estimated flag is
  needed, and "is scanned" is already available via `category.jsonl`'s container.

## How the labels were derived
Compiled from the `segments.jsonl` roles + per-file layout in `category.jsonl` +
direct visual review of a contact sheet of every page's top strip (titles /
headers). Rules, with observed exceptions:
- `candidate_columns` layouts put precincts on the ROW axis → `precincts_present`
  true, `candidate_orientation` columns. `candidate_rows` layouts are per-precinct
  (or county) blocks with candidates in rows and the precinct as a section header,
  not an axis → `precincts_present` false. (So in THIS dataset precincts_present
  is fully correlated with candidate_orientation — a known limitation; add pages
  that break it if the program needs to disentangle them.)
- Contest title: always on `results` pages. `candidate_rows` vendors reprint or
  start a titled contest on every page → true throughout. `candidate_columns`
  vendors drop the title on continuation pages → false, EXCEPT: livingston
  reprints it on its column continuation; calhoun (landscape) reprints the full
  title band on its row continuation; huron's row continuation starts U.S.
  Senator (a new titled contest).
- allegan p31 is the one page with bare data rows — no header band, no candidate
  names, no title (headers_present / candidate_names_present / contest_name all
  false). A good hard negative.

## Corrections made during this pass
- oscoda src14 and wexford src18 were relabeled `continuation-rows` →
  `continuation-columns` in segments.jsonl: their second page shows the
  remaining minor-party candidates (a column spill), not the same candidates
  with new precincts. Both files are therefore both-axes, not row-only.

## Known gaps (for a real single-image program)
- Skew: only 8 scanned pages, and their angles are `null` (unmeasured). Train a
  numeric skew estimator via SYNTHETIC rotation of vector renders (free exact
  ground truth); use the scanned pages as validation. Measure real scanned skew
  (fill in the nulls) before the scanned pages can serve as anything but eyeball
  checks.
- Vector auto-labeling: contest-name / header / rows-vs-cols for vector pages can
  be derived programmatically from pdfplumber word positions to expand the set
  cheaply; only scanned pages need vision/manual labels.
- precincts_present ⟂ candidate_orientation not yet represented (see above).
