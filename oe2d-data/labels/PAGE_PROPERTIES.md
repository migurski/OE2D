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
- `precincts_present` — is there a precinct AXIS on the page?
- `precinct_orientation` — `rows` where present, else null.
- `skew_degrees` — `0.0` for vector renders (exact: vector PDFs rasterize with no
  skew). `null` for scanned pages (not yet measured — deliberately not fabricated;
  the qualitative scan condition is in `fixture-notes.md`). So a number means a
  known/exact angle and `null` means unmeasured; no separate estimated flag is
  needed, and "is scanned" is already available via `category.jsonl`'s container.
- `transform`, `params` — SYNTHETIC rows only (absent on real pages). The
  transform the loader applies to the base render:
  - `rotate` `{degrees, expand, fill}` — rotate by a known angle (positive =
    counter-clockwise, PIL); simulates scanner skew. `skew_degrees` = the angle,
    other labels inherit the base. 80 rows: 10 vector vendors × ±0.5–3°.
  - `crop_top` `{remove_fraction}` — drop the top slice (title + header band),
    leaving bare data rows — a stand-in for a mid-table continuation page.
    `contest_name_present` / `candidate_names_present` / `headers_present` =
    false. 15 rows over diverse vector results pages.
  All bases are vector results pages (crisp, skew-0, full headers).

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

## Why synthetic rows exist
Two properties can't be learned from the real pages alone: **skew** (all vector
renders are exactly 0°, and the 8 scanned pages are `null`/unmeasured — no signal)
and **header/candidate-name absence** (header-omission turned out rare and
vendor-specific — only allegan omits; the green-Clarity family and Electionware
reprint headers every page — leaving 2 real negatives). The `rotate` and
`crop_top` rows supply exact-labeled examples for these. Caveats: synthetic only,
so train-only; `rotate` covers ±3° (real scans can be worse and add non-skew
noise — hole-punches, contrast, handwriting, see fixture-notes.md); `crop_top`
approximates one failure mode of header-absence, not all.

## Known gaps (for a real single-image program)
- Skew: validate on the 8 real scanned pages once their angles are measured
  (fill the nulls); until then they're eyeball checks only.
- Vector auto-labeling: contest-name / header / rows-vs-cols for vector pages can
  be derived programmatically from pdfplumber word positions to expand the real
  set cheaply; only scanned pages need vision/manual labels.
- precincts_present ⟂ candidate_orientation not yet represented (see above).
