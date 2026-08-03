# oe2d.pages — handoff 2: teach the page VLM to name the READ SHAPE

## STATUS: DONE (2026-08-03)

Implemented and validated. `PageAnalysis` gained three read-shape fields —
`contests_across` (single|multiple, the mega-grid detector; named for the horizontal
axis because a STACKED second contest is not one), `precinct_rows`
(single|multiple|none), `value_columns` (total_only|methods|methods_with_percent) —
wired through `PageProperties`, the prediction assembly, `metrics.FIELD_WEIGHTS`
(the three routers at 2.0), and `datasets` (a `"split":"train"` pin so the sole
mega-grid exemplars can't strand in val). All 89 existing pages labeled (fixture-level
structural facts, one representative page viewed per fixture) plus 5 exemplars for the
new label space: Missaukee (vector mega-grid) + Columbia p11 (scanned mega-grid) for
`contests_across=multiple`, Columbia p1 (scanned single), Nevada (report_lines_methods),
Mono (report_lines_total).

The shipped analyzer is now **Qwen3-VL** (committed `optimized_page_analyzer.json`,
temp 0) — strongest per pages-PERFORMANCE.md. Zero-shot on the new task: orientation
99%, contests_across 97%, precinct_rows 97%, precinct_scope 97%, value_columns 67%
(its misses are share-%-vs-method-% gray areas on county/columns pages that DON'T route,
so routing is unaffected). GEPA (Qwen student, 180 calls) was RUN and confirmed a no-op:
it raised the 20-page val to 0.982 (value_columns val 90%) but that was val-overfit —
on the honest full-79 the tuned prompt scored value_columns 63% (< stock 67%), all other
fields identical, so the STOCK Qwen program ships (the committed artifact). This matches
pages-PERFORMANCE.md: GEPA rescues weak models, not a capable one whose seed is saturated.

`votes.detect_dispatch` now maps those fields to the full read_strategy (see
`_propose_read_strategy`). Result on the 50-contest votes gold:
**--detect read_strategy 46% → 74%, orientation 92% → 100%; --detected macro wF1 0.953
/ F1 0.969.** Every newly-detected strategy reads at 1.000 (missaukee flat_multi ×5,
nevada report_lines_methods ×5, mono report_lines_total ×3, branch flat_tables ×4).

Two safety mechanisms make the mapping non-regressing: the flat family is
checksum-confirmed (a wrong ruled_scan/flat_multi falls back to auto — Gogebic's
faint-ruled scan self-corrects to 1.000), and `report_lines_*` got the same
confirm-and-fall-back (an EMPTY report read means the page is some other per-precinct
grammar — Calaveras looks like Nevada's Dominion report but grids cleanly — so it falls
back to the auto read).

**Residual gap to the gold-dispatch 1.000** = single-page-undetectable shapes only:
`flat_grouped` (plumas/ontonagon — candidate columns split ACROSS pages, invisible on
one page) and `ruled_columns` where it looks identical to `auto` (montmorency
president/senate — scanned method-sub-rows indistinguishable from Gogebic, which needs
auto; ruled_columns has no reconcile fallback, so proposing it would break Gogebic).
The `--read-strategy` CLI override remains for these. Closing them needs either a
multi-page probe (flat_grouped) or a reconcile-protected ruled_columns read (needs a
county-total the per-precinct SOVC doesn't print in-range) — deferred.

Commits: `755be2f` (fields + labels + exemplars), `1cecb27` (detect_dispatch mapping +
report_lines fallback + Qwen artifact). Everything below is the original scoping.

---


Pick-up doc for one task: extend `PageAnalysis` so the image VLM reports enough about a page's layout
that `oe2d.votes.detect_dispatch` can propose the right **read_strategy** — not just `auto`/`ruled_scan`
as today. Read `pages-HANDOFF.md` for the module's architecture (composite `PageAnalyzer`, in-module
deterministic skew, GEPA optimize). This doc is the new-field task and its examples.

## Why (the dependency from oe2d.votes)

`oe2d.votes` now reads 8 layouts, each a `read_strategy`. `detect_dispatch(file, page)` picks the
strategy from the page image (so an unseen county needs no hand-set field). Measured today
(`oe2d-votes-evaluate --detect`, 50 gold contests): **orientation 92%, read_strategy only 46%** —
every one of the six NEWER strategies falls to `auto`:

    auto→flat_tables 5 · auto→report_lines_methods 5 · auto→ruled_columns 4 · auto→flat_multi 4 ·
    auto→flat_grouped 4 · auto→report_lines_total 3

So autonomous routing is blocked until the VLM can tell these shapes apart. (A stopgap is in: the votes
CLI `--read-strategy` now accepts all 8, so an OPERATOR can name the shape by hand. This task removes
the need to.)

## Why the current fields can't do it

`PageAnalysis` reports: candidate_orientation, precinct_scope, precinct_orientation, ruled_table,
contest_name_present, candidate_names_present, headers_present (+ deterministic skew, + a text-layer
`scanned` check in detect_dispatch). Those collapse distinct read shapes onto identical field values:

- **rows + per_precinct** is THREE strategies that look the same to the VLM: `auto` (Electionware
  tabular, methods TOTAL/ED/AV/Early — Bay/Adams), `report_lines_total` (Dominion "Election Summary
  Report", a single Total per choice, name wrapped around the value — Mono), `report_lines_methods`
  (Dominion "Precinct Results Report", a count+PERCENT pair per method — Nevada).
- **columns + multi_precinct** is FIVE: `flat_tables` (one contest, one row/precinct, scanned —
  Huron/Columbia), `ruled_columns` (one contest but each precinct has vote-METHOD sub-rows, scanned —
  Montmorency), `auto` (MI SOVC, method sub-rows, vector — Oscoda/Barry/Gogebic/Calhoun), `flat_multi`
  (a MEGA-GRID: several contests side-by-side sharing the precinct rows — Missaukee), `flat_grouped`
  (one contest whose candidate columns are SPLIT across pages that repeat the precincts — Hart SOVC,
  Plumas/Ontonagon).

The distinctions are all VISIBLE on the page; the model just isn't asked about them.

## Proposed new observations (orthogonal; refine while labeling)

Keep the existing "orthogonal facts, combined downstream" design — add small factual fields, let
detect_dispatch combine them. Starting taxonomy (validate against real images; rename/merge as needed):

1. **`contests_on_page`: `single` | `multiple`** — are SEVERAL contests laid out side-by-side, one row
   per precinct spanning them (a mega-grid)? This is the `flat_multi` signal.
2. **`precinct_rows`: `single` | `multiple` | `none`** — on a multi_precinct page, does each precinct
   occupy ONE data row or SEVERAL vote-method sub-rows (Election Day / AV / Total per precinct)?
   Separates flat (single) from method-sub-row reads (multiple). `none` for per_precinct/county pages.
3. **`value_columns`: `total_only` | `methods` | `methods_with_percent`** — the per-candidate number
   columns: a lone Total, several method totals, or count+percent PAIRS. Separates the rows family
   (total_only→report_total, methods_with_percent→report_methods, methods→auto) and flags percent scans.

### detect_dispatch mapping (with orientation + scanned + ruled)

    rows + per_precinct:
        value_columns total_only          -> report_lines_total
        value_columns methods_with_percent -> report_lines_methods
        value_columns methods              -> auto
    columns + multi_precinct:
        contests_on_page multiple          -> flat_multi
        precinct_rows multiple             -> ruled_columns (scanned) | auto (vector)
        precinct_rows single, scanned+ruled -> flat_tables      (flat_grouped caveat below)
    (unchanged) scanned + ruled + flat     -> ruled_scan ;  everything else -> auto

The reconcile checksum stays the safety net: for the flat/columns family (flat_tables / flat_multi /
flat_grouped) `_read_votes` already TRIES the proposal and falls back to `auto` when Σ-precincts ≠ the
printed county Total, so a VLM slip there self-corrects. The ROWS family has no county-total reconcile,
so `value_columns` must be genuinely accurate — weight it and label it carefully.

### The residual: flat_grouped

From ONE page, `flat_grouped` is indistinguishable from `flat_tables` (single contest, one row per
precinct); it differs only ACROSS pages (candidate columns split, precincts repeat). detect_dispatch
would propose `flat_tables`, whose read misses the split-off candidates and FAILS the reconcile.
Options, later: (a) a cheap multi-page probe — do subsequent pages repeat the same precinct labels with
DIFFERENT candidate headers? (b) leave `flat_grouped` gold-only for now. Recommend (b) + a note; it is
the one shape single-page vision cannot settle.

## Examples to add / label (the bulk of the work)

The pages gold is `oe2d-data/pages/training-page-images.jsonl` — **89 images across ~30 counties**
(Berrien, Lapeer, Alameda, Alger, …), and it does NOT yet include the votes-gold new sources. So:

1. **Add page-1 images for the shapes that aren't represented**, at minimum one clean exemplar each:
   - `report_lines_methods` — Nevada CA 2020 (`tmp/new-kinds-2020/nevada-ca-2020-precinct.pdf` p1)
   - `report_lines_total` — Mono CA 2020 (`tmp/new-kinds/optional--mono-ca.pdf` p1)
   - `flat_multi` — Missaukee MI 2024 (`tmp/new-kinds/vector--missaukee-mi.pdf` p1)
   - `ruled_columns` — Montmorency (`tmp/new-kinds/scanned--montmorency-mi.pdf`)
   - `flat_grouped` — Plumas / Ontonagon (Hart SOVC)
   - `flat_tables` — Huron / Columbia ; `auto`-rows — Bay / Adams ; `auto`-columns — Oscoda / Barry
   (render with `oe2d.rendering` / `pdf2image`; the votes gold's `source_url` per contest gives the PDF.)
2. **Label the 3 new fields on ALL images** (89 existing + the additions). Bootstrap: the existing
   fields + `source_fixture` narrow each; render and classify by eye, or pre-fill by running the
   current analyzer for orientation/scope then hand-set the 3 new fields. This is the real effort.

## Do the work

1. `oe2d/pages/signatures.py` — add the new `dspy.OutputField`s + their `Literal` taxonomies to
   `PageAnalysis`; extend the docstring (it is the GEPA seed instruction) with how to decide each.
2. `oe2d/pages/__init__.py` — add the fields to `PageProperties` (CONTENT_FIELDS auto-includes them).
3. `oe2d/pages/metrics.py` — add the new fields to `FIELD_WEIGHTS` (weight `value_columns` /
   `contests_on_page` ~2, like ruled_table/precinct_scope — they route the read).
4. Label `oe2d-data/pages/training-page-images.jsonl` (add images + the 3 fields).
5. `oe2d-pages-evaluate` for per-field accuracy; `oe2d-pages-optimize` (GEPA) to re-optimize — note
   `MultiModalInstructionProposer` is REQUIRED (see pages-HANDOFF.md) or the image blows the reflection
   context. Writes `oe2d/pages/model/optimized_page_analyzer.json`.
6. **Then the votes side** (small): in `oe2d/votes/__init__.py` `detect_dispatch`, replace the coarse
   `'ruled_scan' if (scanned and ruled) else 'auto'` with the mapping above, reading the new
   `analyze_page` fields. Acceptance test: `oe2d-votes-evaluate --detect` (read_strategy match climbs
   toward 100% barring flat_grouped) AND `--detected` (end-to-end score stays 1.000 — the reconcile
   fallback protects the flat family). Return to `votes-HANDOFF-5.md` once this passes.

## Pointers

Pages module: `signatures.py` (the signature), `__init__.py` (`PageAnalyzer`, `PageProperties`,
`analyze_page`, `build_analyzer`, `LM_LLAMA4_MAVERICK`, `OPTIMIZED_MODEL_PATH`), `metrics.py`
(`FIELD_WEIGHTS`, prose feedback), `datasets.py` (loads the jsonl → Examples, split by fixture),
`optimize.py` (GEPA), `oe2d-data/pages/training-page-images.jsonl` (labels + `images/`). Consumer:
`oe2d.votes.detect_dispatch` (the one place to map the fields → read_strategy). Every read_strategy and
its exemplar county is in `votes-HANDOFF-5.md` + `datasets.load_index()`.
