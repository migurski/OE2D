# oe2d.contests — handoff 4: electoral_context is free-form prose = the whole-county slate

Continues `contests-HANDOFF-3.md` (that one covered the Bedrock LM migration; the contests locator
now ships on **Haiku 4.5**, `LM_CLAUDE_HAIKU45`, via `build_locator`). This doc covers the
**electoral-context shape** work. Branch: `migurski/categorize-sources`. Votes is deliberately out
of scope here (see the bottom note).

## The mental model (what "context" is)

`electoral_context` is the **free-form prose a caller supplies in real life** — the county's full
candidate slate, exactly as it sits in the `candidates/2024/general/<State>/<County>.txt` files
(`candidates/README.md` documents that directory). A caller hands over the **whole county slate per
document** — every federal race — regardless of which contest is being located. So when locating a
U.S. House race the context still includes the presidential and Senate lines. Example (any Alameda
target now carries the same block):

```
Candidates for president were Kamala Harris (DEM), Donald Trump (REP), Robert F. Kennedy Jr. (AIP), Jill Stein (GRN), and Chase Oliver (LIB)
Candidates for U.S. Senate (full term) were Adam Schiff (DEM) and Steve Garvey (REP)
Candidates for U.S. Senate (partial/unexpired term) were Adam Schiff (DEM) and Steve Garvey (REP)
Candidates for U.S. House District 10 were Mark DeSaulnier (DEM) and Katherine Piccinini (REP)
... (all four Alameda districts) ...
Candidates for State Assembly District 18 were Bonta and Sandford
```

## The gold-shape audit (why this arc happened)

All three modules take an `electoral_context` input (renamed uniformly from `context`/
`candidate_context` in commit `5d93998`; CLIs are `--electoral-context`). We audited whether each
module's GOLD stores context the way it's actually provided (prose):

- **pages** — already aligned: stores the real whole-county prose, consumed as prose. The model.
- **contests** — was NOT aligned; fixed this arc (below).
- **votes** — NOT aligned and structurally coupled (its deterministic column-matcher parses a
  `"- Name (PARTY)"` list via `splitlines` at ~6 sites). Left for later, on purpose.

## What changed in contests (this arc)

1. **`7dbf482`** — the gold stored a `candidates` ARRAY and `row_target` synthesized a template
   (`"<office> race; candidates include ..."`). Nothing consumed the array structurally (only
   `row_target` read it), so it was pure indirection producing a stand-in that didn't match how
   context arrives. Replaced the array with an `electoral_context` prose string; `row_target` now
   passes it through. `from_votes.context_prose` emits the same shape.
2. **`6794d98`** — the per-target prose was still too narrow (one race). Rebuilt each DOCUMENT's
   context as the **whole county slate** (president always present), sourced from the directory
   content for 2024 and from provided values for 2020, with the document's state-level targets
   folded in from the gold's own data. Shared across all of a document's targets. 106 full-document
   + 24 fixture rows.
3. Party correctness (mostly in the votes commits `11a03ee`/`58c8b41`, but the directory the
   contests context is built from now uses **state ballot party codes**: Kennedy `NLP` in MI / `AIP`
   in CA, U.S. Taxpayers `UST`, Working Class `WCP`, Green `GRN`).

## How the contests context is produced (important)

- **Runtime never reads `candidates/`** — the locator reads only the gold's `electoral_context`
  field (via `row_target`). This is a firm constraint (Mike's).
- The gold's `electoral_context` was **populated offline** (a scratch step) FROM the directory
  content: for a 2024 document, the whole `candidates/2024/general/<State>/<County>.txt`; for 2020
  CA docs (Calaveras/Plumas/Mono/Nevada) a synthesized slate (president + the county's races). 2020
  president uses the provided 4 majors PLUS `Gloria La Riva (PFP)` and `Rocky De La Fuente Guerra
  (AIP)` — Mike's note: relying on 3rd-tier candidates being present in real context is optimistic;
  included only to close the eval gap.
- State-level races the directory doesn't cover (State Assembly/Senate, State Board of Education,
  Wayne State Univ. Board of Governors, Attorney General, Straight Party) are folded in from the
  gold's own candidate data — real, not fabricated.

## Straight Party / non-federal targets — a settled nuance

A line like `"Candidates for Straight Party were Democratic, Republican, ... and Write-ins"` is NOT
from `candidates/` (federal-only); it's synthesized from the Barry votes-derived Straight Party
target. The "Candidates for …" wording is a loose fit for a party-ticket mechanism, but for the
LOCATE task this is FINE and useful: Straight Party is a real contest to find, and listing its party
options is exactly the confirming signal `MatchContestTitles` wants. Do NOT "fix" it by dropping or
rewording — that's a votes/extraction concern, not a contests/locate one. Cosmetic only.

## In flight: the before/after eval (background `bkdb67i0m`)

Running the full-documents contests eval on the shipped Haiku, isolating the whole-county context
change on the SAME 106-target gold:
- **after** = current whole-county context (HEAD `6794d98`)
- **before** = per-target context from `7dbf482` (the script `git show`s that version into the gold
  file, runs, then `git checkout HEAD --` restores the committed whole-county gold)

Outputs: `contests-ctx-after.txt`, `contests-ctx-before.txt`. Metric = page-set recall/precision/F1
per target (macro), plus title-hit rate. ~2h/run (ReAct match per target over 42 docs; **no OCR
cache** — contests locate re-OCRs with free tesseract every run), ~4h total. The question it answers:
does the whole-county slate (president always present, other races included) help / hurt / not move
contest-locate accuracy vs a single-race context. **Result still pending** — read the two files (or
the macro lines) when `bkdb67i0m` completes; confirm the gold file was restored clean afterward.

## Open items / next

1. **Read the before/after result** and decide whether whole-county context stays (it will unless it
   regresses locate meaningfully).
2. **votes decoupling** (deferred): votes can't consume free-form prose until its deterministic
   column-matcher stops assuming the `"- Name (PARTY)"` per-line list — a real refactor.
3. Optional cosmetic: reword non-federal `"Candidates for <mechanism>"` lines — not needed for locate.

## Don't relitigate

- Contests ships on Haiku (handoff 3). Eval uses `build_locator` (Haiku) by default.
- Runtime doesn't read `candidates/`; the gold carries the prose.
- The gold's `candidates` array is GONE; `electoral_context` prose is the single source.
- pages is already correct; votes is knowingly the last un-aligned module.
