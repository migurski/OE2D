# page_properties.jsonl — per-page (single-image) labels

Training set for a **single-image** DSPy program that reports in-page facts to
guide extraction (e.g. hint the pdfplumber table finder) — distinct from the
per-*file* source categorizer (`category.jsonl`) and from inter-page /
whole-document stitching, which live at other levels.

155 rows: **60 real** pages (from the re-excerpted fixtures) plus **95 synthetic**
pages. A row is synthetic iff it carries a `transform` — the loader renders the
base page and applies the transform to produce an image with an exactly known
label. Keep synthetic rows in the TRAIN split only; validate on real pages
(filter: `transform` present → train-only).

Join to an image by rendering `path` page `fixture_page` (1-based). `path` is
relative to THIS file's directory (`oe2d-data/labels/`), e.g.
`../fixtures/categorize/<name>.pdf`; `segments.jsonl` uses the same convention.

**Render DPI:** rasterize at a resolution that keeps the DENSEST target legible.
The dense landscape SOVC tables (e.g. calhoun — many candidate columns) need
~300 DPI; rendering.py's 220 default undersamples them (text and gridlines blur).
Simpler pages are fine at 220. Use the same DPI for synthetic bases and real
pages, and at inference, so training and serving match. (Same caveat applies to
the categorizer's `inspect_page`, which currently renders at 220 — a candidate
follow-up if its vision reads of dense pages look soft.)
`source_page` is the page number in the upstream original (`segments.jsonl`), or
null for synthetic rows.

## Fields
- `path`, `fixture_page`, `source_page`, `role` — identity + window role
  (`results` / `continuation-columns` / `continuation-rows`; null for synthetic).
- `orientation` — the fixture's overall candidate orientation (from category.jsonl).
- `candidate_orientation` — candidates on THIS page in `columns` or `rows`.
- `contest_name_present` — is a contest title visible on this page?
- `candidate_names_present` — are candidate/party names visible on this page?
- `headers_present` — are column/row headers (labeling the numbers) present?
- `precinct_scope` — the page's precinct dimension: `multi_precinct` (many
  precincts along an axis, e.g. SOVC rows), `per_precinct` (the page IS one named
  precinct, identity in the banner/header), or `county` (aggregate totals, no
  precinct). Replaces the old `precincts_present` boolean, which was fully
  determined by `candidate_orientation`; `precinct_scope` splits the candidate-
  rows pages into per_precinct vs county, information orientation never carried.
- `precinct_orientation` — the precinct AXIS direction, only when
  `multi_precinct`: `rows` (today) or `columns` (once transposed layouts are
  added); null for `per_precinct` / `county`.
- `skew_degrees` — `0.0` for vector renders (exact: vector PDFs rasterize with no
  skew). `null` for scanned pages (not yet measured — deliberately not fabricated;
  the qualitative scan condition is in `fixture-notes.md`). So a number means a
  known/exact angle and `null` means unmeasured; no separate estimated flag is
  needed, and "is scanned" is already available via `category.jsonl`'s container.
- `transform`, `params` — SYNTHETIC rows only (absent on real pages). The
  transform the loader applies to the base render:
  - `rotate` `{degrees, expand, fill}` — rotate by a known angle (positive =
    counter-clockwise, PIL); simulates scanner skew. `skew_degrees` = the angle,
    other labels inherit the base. 80 rows: 10 vector vendors × ±0.25–1.5° (kept
    small — real scans are only slightly tilted).
  - `crop_top` `{remove_fraction}` — drop the top slice (title + header band),
    leaving bare data rows — a stand-in for a mid-table continuation page.
    `contest_name_present` / `candidate_names_present` / `headers_present` =
    false. 15 rows over diverse vector results pages.
  All bases are vector results pages (crisp, skew-0, full headers).

## How the labels were derived
Compiled from the `segments.jsonl` roles + per-file layout in `category.jsonl` +
direct visual review of a contact sheet of every page's top strip (titles /
headers). Rules, with observed exceptions:
- `candidate_columns` layouts put precincts on the ROW axis → `precinct_scope`
  multi_precinct, `precinct_orientation` rows. `candidate_rows` layouts are either
  per-precinct blocks (candidates in rows, precinct named in the header →
  `per_precinct`) or county aggregates (→ `county`); neither has an in-page
  precinct axis, so `precinct_orientation` is null. `precinct_scope` thus
  decorrelates from orientation on the candidate-rows side (per_precinct vs
  county). The AXIS direction and the columns side are still correlated (all
  candidate-columns pages are multi_precinct/rows) — no candidate-columns
  single-precinct/county pages and no precincts-as-columns pages exist in the
  corpus yet; those need real off-diagonal data (likely spreadsheets).
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

## Why synthetic rows exist
Two properties can't be learned from the real pages alone: **skew** (all vector
renders are exactly 0°, and the 8 scanned pages are `null`/unmeasured — no signal)
and **header/candidate-name absence** (header-omission turned out rare and
vendor-specific — only allegan omits; the green-Clarity family and Electionware
reprint headers every page — leaving 2 real negatives). The `rotate` and
`crop_top` rows supply exact-labeled examples for these. Caveats: synthetic only,
so train-only; `rotate` covers only ±1.5° (real scans are slightly tilted) and
is CLEANER than a real scan — it omits scan noise (hole-punches, contrast,
handwriting, speckle; see fixture-notes.md); `crop_top` approximates one failure
mode of header-absence, not all.

## Known gaps (for a real single-image program)
- Skew: validate on the 8 real scanned pages once their angles are measured
  (fill the nulls); until then they're eyeball checks only.
- Vector auto-labeling: contest-name / header / rows-vs-cols for vector pages can
  be derived programmatically from pdfplumber word positions to expand the real
  set cheaply; only scanned pages need vision/manual labels.
- precinct AXIS (rows vs columns) and the candidate-columns side still track
  orientation: need real pages with precincts as COLUMNS (transposed layouts) and
  candidate-columns pages that are single-precinct or county. Most likely found in
  the spreadsheet modality (precincts often run across the top). `precinct_scope`
  already decorrelates the rows side without new data.
