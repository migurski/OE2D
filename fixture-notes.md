# Fixture review notes (Mike's observations)

Purpose: characterize how multi-page contest grids continue, to define training
examples. To be turned into data later.

---

# >>> RESUME HERE (handoff, effort paused) <<<

## Goal
Replace the hasty front-sampled 4-page fixtures with faithful CONTEST-RESULTS
page windows drawn from the UPSTREAM full originals, so the training set relies
on page CONTENT (candidate names + vote values), not on vendor familiarity.
Keep the chosen files and Mike's labels; only fix WHICH pages represent each.

## Locked decisions
- Commit contiguous re-excerpted PDFs (overwrite the existing fixture file so the
  category.jsonl path mapping stays intact) + a provenance manifest.
- Manifest = oe2d-data/labels/segments.jsonl, one JSON line per fixture:
  {file, source_url, source_pages:[...], contest, roles:{page:role}}.
  roles: "results" (contest-name page), "continuation-columns" (same precincts,
  more candidate cols), "continuation-rows" (columns restart, next precincts).
- Segment = BOUNDED WINDOW (~4pp) showing the split(s); vote values may be
  incomplete on purpose.
- Tool PROPOSES windows, Mike CONFIRMS per file / per batch.
- Vary the contest across files, but only BIG races (President, US Senate, US
  House, State House/Senate, statewide boards/regents/trustees, Gov of Wayne St,
  AG, etc.) — never small county races.
- Do NOT put fixture-generation code in the oe2d package (Mike). Throwaway
  scripts only, in scratchpad (or a top-level scripts/ if ever justified).

## DONE (committed b165146, on branch migurski/categorize-sources)
- barry-mi-sovc-official-results.pdf -> President window, upstream pages 22-25
  (both-axes split; portrait; 3 candidate-col pages wide; name only on p22).
  Label fix: has_side_by_side true->false.
- calhoun-mi-2024-sovfull.pdf -> President window, upstream pages 17-18 (row-only
  split; all candidates fit one page width; title+headers repeat each page).
  Label fix: has_side_by_side true->false.
- has_side_by_side means >=2 CONTESTS side by side; a single-contest window is
  always false, even with many candidate columns. (Mike ruled; apply to all.)
- Added fixture-notes.md (this file), segments.jsonl, oe2d-render-page CLI
  (commit 010d4df), and the earlier rendering CLI work.

## DONE: batch of 10 vector SOVC files (Mike confirmed each via qlmanage)
All re-excerpted from upstream, fixtures overwritten, has_side_by_side->false,
manifest rows appended. Windows (source page ranges), varied big races:
  lapeer     President                       7-10   both-axes w=3
  baraga     President                       10-13  both-axes w=3
  antrim     Governor of Wayne State U       27-29  both-axes w=3 (col-split only;
                                                     contest ends p29, no row page)
  charlevoix U.S. Senator                    13-15  both-axes w=2
  houghton   Rep. in Congress 1st            19-21  both-axes w=2
  oscoda     U.S. Senator                    13-14  row-only
  wexford    Rep. in Congress 1st            17-18  row-only
  alger      Rep. in State Legislature 109th 23-24  row-only
  allegan    State Board of Education        30-31  row-only (row-cont page does
                                                     NOT repeat headers — identity
                                                     only on the results page)
  jackson    President                       36-39  both-axes w=3 (my detector
                                                     over-counted w=6; real w=3, so
                                                     p36-39 IS a full both-axes win)
Corrections learned here: (a) header-repeat on row-continuation pages VARIES by
vendor (calhoun/oscoda/wexford repeat; allegan/barry do NOT); (b) the precinct-
repeat width detector can over-count — always eyeball both-axes cuts.

## DONE: variety batch of 4 non-SOVC PDF vendors (Mike confirmed each)
Deliberately picked to add vendor/layout variety beyond the MI SOVC tool:
  livingston  green-columns (Clarity/Scytl): Electors of President, col-continuation
              p6-7. Contest NAME repeats on the continuation page (3rd header-repeat
              behavior seen). stitch=true (single table split by columns).
  hillsdale   green-ROWS: per-precinct block of STACKED contests spanning pages,
              candidates in rows (Choice|Party|Early|AVCB|ElectionDay|Total).
              p1-2. rotated_headers true->FALSE (headers are horizontal).
              stitch stays false (no single contest split; contests each complete
              on a page, only the precinct's set of contests continues).
  elk (PA)    Electionware, candidate-rows: Presidential Electors with EXPLODED
              write-in rows (DONALD TRUMP, GOD, BUDDY THE BIRDY, ...) that push the
              single contest across p1-2. stitch=true (single contest split).
  adams (PA)  Electionware, candidate-rows: COLLAPSED write-ins so each contest is
              compact; contests STACKED (President, then Senator+AG on p2). p1-2.
              stitch stays false (no split contest).
Rulings applied (Mike): stitch = a SINGLE table/contest split across pages, NOT a
precinct's stacked contests merely continuing onto the next page.
Vendor map learned: livingston + genesee + ionia + ottawa are the SAME green
Clarity-style vendor (candidate columns) in different report-title wrappers
("Statement of Votes Cast" / "Official Canvass Report" / "...precinct-level
results"); hillsdale is the green candidate-ROWS variant. Electionware (elk,
adams; also armstrong/beaver/northampton/snyder) is the PA candidate-rows family,
split by write-in explosion behavior.

## DONE: variety batch 2 of 3 more PDF vendors
  berrien   "Election Summary Report" (serif, no banding), COUNTY-level rollup:
            candidate ROWS, Total-only columns, contests stacked several per page
            (President+Senator p2 -> Congress 4th/5th + State Leg 37th p3). p2-3.
  armstrong same serif "Election Summary Report" vendor but PER-BORO (Apollo Boro):
            per-method columns (Election Day/Mail/Provisional/Total) + separate
            write-in sub-table per contest; stats + President -> Senator/AG/Auditor.
            p1-2. grain county->PRECINCT (it's a per-boro summary, not a county roll).
  emmet     "Precinct Summary Results Report" (plain): candidate ROWS with
            Absentee / Early Voting columns; per-precinct block of stacked contests
            (stats+Straight Party+President -> Senator/Congress/State Leg). p1-2.
All stitch=false (stacked contests continue across pages; no single table split).

## VENDOR MAP so far (which fixtures share software) — avoid re-doing duplicates
- MI SOVC tool (candidate COLUMNS, rotated headers): barry, calhoun, lapeer,
  baraga, antrim, charlevoix, houghton, oscoda, wexford, alger, allegan, + the
  scanned ones (gogebic, kalkaska, mackinac, montcalm, montmorency, otsego,
  st-clair, benzie-scanned, allegan-scanned).
- Green Clarity-style COLUMNS: livingston, genesee, ionia, ottawa (diff report
  titles, same layout). [livingston DONE]
- Green Clarity-style ROWS: hillsdale, clinton, beaver(PA, Electionware footer
  though). [hillsdale DONE]
- Electionware "Summary Results Report" ROWS: elk, adams, bay, mason, montour,
  northampton, snyder. [elk, adams DONE]  Write-in explosion splits the family:
  exploded (elk) spills one contest across pages; collapsed (adams) stacks.
- Serif "Election Summary Report" ROWS: berrien(county), armstrong(boro),
  bedford(PA primary). [berrien, armstrong DONE]
- "Precinct Summary Results Report" (Absentee/Early cols): emmet. [DONE]
Remaining unique-ish still TODO: the green/Electionware duplicates are LOW
marginal value.

## DONE: bedford (PA closed PRIMARY, county-level serif Election Summary Report)
Window p2-3: President of the United States (DEM) p2 -> United States Senator
(DEM) p3. Distinctive: CLOSED PRIMARY so each contest is party-tagged "(DEM)"
(the REP versions are elsewhere, p9+, non-contiguous so not in the window);
COUNTY-level (Precincts Reported 40 of 40, single Total column); candidate rows;
huge EXPLODED write-in lists. Label unchanged (candidate_rows, county, all flags
false) — each contest is one-per-page here, no stacking or single-contest split.

## SCANNED BITMAP QUALITY notes (Mike: come back to this later)
Per-file scan condition observed while re-excerpting the scanned batch (matters
for Textract/OCR downstream, and possibly a future has_* property or a
pre-deskew step):
- gogebic: MODERATE skew; crescent hole-punch / binder marks across the top of
  every page; earlier saw handwritten "Official" annotations. Legible but tilted.
- mackinac: GOOD scan quality overall, some skew. (Mike). NB filename says
  "Closed Primary Nov 11" but CONTENT is the Nov 5 GENERAL (Harris/Trump) —
  mislabeled source filename, not actually a primary.
- huron: VERY HIGH scan quality, NO skew (Mike). Compact per-precinct vendor,
  candidate columns fit one width; President rows continue p2->p3 then United
  States Senator starts on p3 (row-continuation + stacked next contest).
- cass: GOOD scan quality, very minimal skew (Mike). Candidate ROWS, TWO contests
  side-by-side per page, stacked; cover sheet on p1 ("CANVASS OF VOTES CAST").
- (fill in for the deferred scanned twins: kalkaska x2, montcalm, montmorency,
  otsego, st-clair, benzie-scanned, allegan-scanned x2.)
General: these scans render via pdfium at ~90-220 DPI; text is small and light,
faint dotted gridlines, variable contrast. page_words returns EMPTY (no text
layer) so all detection is visual. Skew VARIES page-to-page within a file.
TODO later: quantify skew, decide whether to record a scan-quality/tilt signal,
and whether fixtures should be deskewed before OCR.

## DEFERRED (need separate handling, NOT started)
- ionia, livingston, ottawa, genesee, hillsdale: no big race detected by the
  keyword/"(Vote for" scan — different title format. ionia is the GREEN-BANDED
  data-first vendor (its excerpt already shows real data; may need little/no fix).
  Investigate each: dump page_words on early pages to see the title phrasing.
- alcona (6pp), arenac (5pp), benzie-11-5 (9pp): small county docs with NO
  statewide races; keyword falsely matched local Trustee/Treasurer. Decide per
  file whether they even need re-excerpting.
- SCANNED SOVC (gogebic, benzie-scanned, kalkaska, mackinac, montcalm,
  montmorency, otsego, st-clair, allegan scanned twin): page_words is EMPTY, so
  the text method fails — need a VISION (inspect_page) pass to find results
  pages. Second phase.
- bedford-pa (primary, summary-first): re-excerpt to its candidate-rows results
  pages (the rowspan party-elector detail is deeper than page 1).
- The Electionware PA files (elk, adams) and ionia already show data on page 1 —
  lower priority; verify their excerpts capture a multi-page sequence.

## How to rebuild the throwaway tooling (scratchpad is gone on resume)
Env: .venv-linux ; run modules with the venv python. Fetch upstream via the URL
in oe2d-data/labels/seed_sources.tsv, matched to a fixture by slugifying the
seed 'file' name (lower, non-alnum->'-', [:80]) == fixture basename stem.
Detection recipe (all from oe2d.source_table.page_words — do NOT rely on
page_table; it returns nothing on these unruled SOVC pages):
- Contest-start page: joined page text contains "(Vote for" or an office keyword;
  the contest TITLE is the text before "(Vote for" (strip the running header
  "Page: N of M <date> <time>").
- WIDTH (candidate-col pages per precinct block) = count of CONSECUTIVE pages from
  the start that share the same FIRST PRECINCT NAME. Get the first precinct by
  grouping words into lines (bucket by top), and on each line take the text
  BEFORE the first numeric token; keep the first line whose name contains
  "City of"/"Township"/"Ward"/"Village" or matches /Precinct \d/.
- Window: width==1 (row-only) -> [start, start+1]; width>1 (both-axes) ->
  [start .. start+width] (name + column continuations + first row continuation),
  optionally capped for very wide contests.
Cut with pypdf (add_page for 0-based indices), write, overwrite fixture.

## GOTCHAS (learned the hard way)
- Page ORIENTATION does NOT predict the variant: landscape lapeer splits columns
  (both-axes), portrait allegan fits all candidates (row-only). Use WIDTH, not
  orientation.
- pdfplumber reads ROTATED headers REVERSED ("sirraH"=Harris, "ytraP"=Party) —
  useful as a rotated-headers signal, but breaks left-to-right token order.
- Some SOVC pages have TWO "Precinct" tokens per row and leading Times
  Cast/Registered/Total Votes summary columns, which poison naive column
  signatures — that's why the precinct-repeat width method is used instead.
- For both-axes contests the contest NAME is only on the first page; row-only
  contests repeat the name+headers every page.
- Excerpting SEVERS sequences; always go to the upstream original.
- SCANNED files can be tilted/skewed and carry handwriting/hole-punch noise.

---

## barry-mi-sovc-official-results.pdf
- Container: vector_pdf, candidate_columns, precinct grain.
- Labels: rotated_headers + side_by_side + multi_sheet_stitch.
- ONE table spanning pages 1, 2, 3 (NOT page 4 — page 4 is something else).
- Table is narrow; continuation is by ROWS (many precinct rows), not by adding
  candidate columns.
- Headers repeated on each page.
- => "row-continuation" flavor: same contest, more precincts down the pages.

## benzie-nov-5-2024-statement-of-votes.pdf
- Container: scanned_pdf, candidate_columns, precinct grain.
- Labels: rotated_headers + side_by_side + multi_sheet_stitch.
- Two pages that are DISCONTINUOUS from each other.
- The page on the 3rd sheet (labeled "page 1") likely continues onto the
  FOLLOWING page in the ORIGINAL upstream source — but that continuation page is
  NOT in our fixture excerpt. Would need to find the full source on GitHub to get
  the real sequence.
  => Fixture excerpting broke the sequence; a good stitch example needs the
     upstream original, not the trimmed fixture.
- Scanned doc is slightly TILTED (skew). Worth capturing as a property we may
  want to train on later (tilt/skew detection).

## calhoun-mi-2024-sovfull.pdf
- Container: vector_pdf, candidate_columns, precinct grain.
- Labels: rotated_headers + side_by_side + multi_sheet_stitch.
- Very similar to barry-mi: single table across first three pages, precincts on
  the LEFT, row-continuation.
- Likely generated from IDENTICAL upstream software as barry-mi (same SOVC tool).
  => Many of the MI vector SOVC files are near-duplicate layouts; one exemplar
     may cover the whole cluster for training.

## elk-pa-generalpsummary2024.pdf
- Container: vector_pdf, candidate_ROWS, precinct grain (PA, different vendor
  from the MI SOVC cluster).
- Labels: rotated_headers + side_by_side + multi_sheet_stitch.
- Layout is PER-PRECINCT: precinct name at TOP of the sheet, candidates listed
  down the ROWS.
- First three pages = the SAME single precinct (presidential contest), spanning
  by ROWS because of MANY write-in candidates (mostly useless write-ins).
  Table headers repeated on each page.
- Fourth page is from LATER in the document: a DIFFERENT precinct, listing some
  write-ins.
  => Continuation here is "one precinct's long candidate list across pages,"
     NOT a precinct-row grid. Distinct from the MI SOVC row-continuation.
  => Excerpt again broke continuity (page 4 jumps to another precinct).
  => Training signal: write-in rows are noise; page boundary != new
     precinct/contest boundary (a precinct can span pages).

## 2024-adams-county-pa-precinct-summary-general-2024.pdf
- Container: vector_pdf, candidate_ROWS, precinct grain.
- Labels: stacked_contests ONLY (single-page-multi-contest category).
- SAME upstream software as elk-pa (per-precinct summary report style).
- First three pages are SEQUENTIAL for a SINGLE precinct: multiple different
  contest tables shown one after another (stacked), FIVE contests total across
  the first three pages.
- Fourth page is from DEEPER in the document: a DIFFERENT precinct, two contests.
  => "stacked contests within one precinct block, block spans pages" — the
     stacked_contests flag AND cross-page continuation both apply, but this row
     was NOT flagged multi_sheet_stitch. Possible label inconsistency vs elk-pa
     (which was flagged stitch for the same vendor/structure).
  => Same excerpt artifact: page 4 jumps to another precinct.

## PDF coverage map (48 PDFs, 12 distinct orientation+flag combos)
Well covered: [27] cols+rotated+side_by_side+stitch (MI SOVC; saw barry,
calhoun, benzie-scanned, gogebic); [9] rows+stacked (PA/MI precinct summary;
saw adams).
Uncovered singleton combos to sample: cols+stacked+stitch (branch,huron);
rows+stacked+stitch (mason,montour); rows+stacked+side_by_side (cass,scanned);
cols+ALL4 (ionia); cols+rotated+stitch (livingston); rows+rotated+stitch
(genesee); rows+rotated+stacked+stitch (benzie-11-5-results, NOT the scanned
benzie we saw); rows+rotated+stacked (hillsdale); rows+PLAIN (bedford, baseline).

## 2024-ionia-county-mi-precinct-level-results.pdf
- Container: vector_pdf, candidate_columns, precinct grain.
- Labels: ALL FOUR flags (rotated + stacked + side_by_side + stitch).
- Page 1: a COVER SHEET (new property, not in current flag set — table of
  contents / cover, no data).
- Pages 2-3: a SINGLE table split HORIZONTALLY across the two pages — precincts
  in rows, candidates in columns, rotated headers so the many candidate columns
  fit. => COLUMN-continuation (contest too WIDE), the horizontal counterpart to
  Barry's ROW-continuation. This is the "side_by_side + stitch means candidates
  spill rightward onto the next page" case.
- Page 4: a few contests STACKED.
- => single file exercises three distinct behaviors: cover sheet, horizontal
     column-split table, stacked contests. Rich exemplar.
- NEW property to consider tracking: has_cover_sheet / table-of-contents page.

## Two distinct STITCH directions (important distinction for training)
- ROW-continuation (vertical): same contest, MORE PRECINCT ROWS down the pages;
  table stays narrow, headers repeat. E.g. barry, calhoun.
- COLUMN-continuation (horizontal): same contest, MORE CANDIDATE COLUMNS across
  the pages; table too wide, rotated headers. E.g. ionia pages 2-3.
- A stitch example must specify WHICH direction it demonstrates.

## bedford-pa-officialelectionsummarydistrictreportrpt.pdf
- Container: vector_pdf, candidate_rows, precinct grain (labeled DISTRICT report).
- Labels: PLAIN (none of the four flags) — the only such PDF.
- Pages 1-3 are in sequence from the original.
- Weird format: candidates represented as PARTY ELECTORS ("DEMOCRATIC",
  "REPUBLICAN") with ROWSPAN=4, categorized votes for each across later columns,
  then a lengthy list of write-ins with small vote counts.
- Mike suspects MISCATEGORIZED: if pages 1-3 continue in sequence it probably
  DOES span pages (stitch), contradicting the "plain" label. => revisit label;
  likely should carry multi_sheet_stitch.
- NEW structural wrinkle: merged/rowspan cells (party elector rows spanning 4
  sub-rows). Not captured by any current property.

## branch-mi-results-per-precinct-2.pdf  (Mike + Claude viewed images)
- Container: vector_pdf, candidate_columns, precinct grain.
- Labels: stacked_contests + multi_sheet_stitch (NO rotated_headers).
- Contests both STACKED and MULTI-PAGE, with NO repetition of the column header
  on subsequent stitched pages (Mike).
- Claude viewed p1-p2:
  - p1: one contest "Straight Party (Vote for 1)", precincts in rows, 7 party
    columns, two-line HORIZONTAL headers (not rotated), ends in Total row.
  - p2: "President/Vice-President" (candidate columns incl. a SINGLE collapsed
    WRITE-IN column) -> Total, then "United States Senator" starts on the SAME
    page. Two contests stacked vertically.
- DISCRIMINATOR: this vendor COLLAPSES write-ins into one column, vs elk/bedford
  which EXPLODE write-ins into many low-count rows. Distinguishes the two report
  families and explains their different spill behavior.

## GEPA feedback notes (Mike: store for optimization later; not necessarily flags)
- Merged/rowspan cells exist (bedford: party-elector rows with rowspan=4). Do NOT
  need a flag for it, but feed to GEPA as a known structural wrinkle.
- Write-in handling splits into two families: COLLAPSED single write-in column
  (branch-style) vs EXPLODED per-write-in rows (elk/bedford-style). Write-in
  explosion is what forces page-spill (see adams-vs-elk resolution).
- Scanned files can be TILTED/skewed (benzie, gogebic) -> Textract challenge.
- Cover / table-of-contents pages occur (ionia p1) and the vision inspector
  prompt already asks about them, though there is no flag.
- Excerpted fixtures often SEVER real page sequences (page 4 jumps deep into the
  doc); true multi-page stitch training likely needs UPSTREAM originals.
- Stitch has TWO directions: row-continuation (more precincts, narrow table,
  repeated headers) vs column-continuation (more candidates, wide table, rotated
  headers, ionia p2-3).

## CRITICAL: excerpt pages often don't show their own labeled features
(Claude rendered + viewed the fixture pages directly.)
All MI SOVC fixtures (barry, benzie-scanned, gogebic; likely calhoun etc.) OPEN
with a TURNOUT / PARTICIPATION SUMMARY section, NOT the candidate grid:
  columns = Precinct | Registered Voters | Voters Cast | % Turnout
  rows    = precincts (barry+gogebic also nest per-method sub-rows:
            Election Day / AV Counting Boards / Early Voting / Total).
The candidate_columns + rotated_headers + side_by_side labels describe the
CANDIDATE section, which is deep in the ORIGINAL and did NOT survive the 4-page
excerpt. Page counters seen: barry "1 of 394", benzie "1 of 169", gogebic
"1 of 145".
=> IMPLICATION for image-classification training: the rendered fixture pages do
   NOT contain the features their labels assert. Training on them would teach the
   model to predict rotated_headers/side_by_side from pages that show neither.
   For real image training we almost certainly need the CANDIDATE pages from the
   UPSTREAM originals, and/or per-PAGE labels rather than per-FILE labels.

Scan-noise details (Claude view):
- gogebic (scanned): handwritten "Official" annotations, hole-punch / crescent
  binder marks at top. Real scan clutter beyond tilt.
- Skew VARIES page-to-page: gogebic p1 and benzie p3 are not badly tilted even
  though Mike flagged tilt elsewhere in those files.

## Per-file render findings (Claude viewed pages)
- elk-pa (Electionware): p1 shows precinct BENEZETTE — STATISTICS block then
  "Presidential Electors / Vote For 1" as candidate ROWS; Write-In Totals
  EXPLODES into a long individual list (NO CONFIDENCE, MICKY MOUSE, TULSI
  GABBARD, ADOLF HITLER, ...). Footer "Page 1 of 613". Excerpt DOES show label.
- adams-pa (Electionware, SAME vendor as elk): p1 precinct Abbottstown — same
  STATISTICS + Presidential Electors rows, but Write-In Totals = just
  "Not Assigned" (0), then Total Votes Cast / Overvotes / Undervotes / Contest
  Totals. No exploded write-ins => fits a page, no spill. Footer "Page 1 of 255".
  Confirms the write-in-explosion resolution VISUALLY.
- ionia-mi (green-banded vendor, NOT the SOVC tool): fixture p2 = "Straight
  Party Ticket" with precincts in ROWS and party COLUMNS bearing ROTATED
  vertical headers, + Cast/Under/Over/Ballots/Registered cols, Totals row.
  Internal label "Page 1" (fixture kept cover + first data page). Excerpt DOES
  contain its labeled features (rotated headers, side_by_side).
- bedford-pa: OUTLIER — "Election Summary Report", CLOSED PRIMARY, Bedford
  County, APRIL 23 2024 (a PRIMARY; only non-Nov-general file in the set). p1 is
  a party/counting-group ballot summary (Elector Group x Election Day/Mail-In/
  Provisional/Total), NOT candidate rows. Footer "Page 1 of 35". Like MI SOVC,
  the excerpt preamble does not show its candidate_rows label; rowspan
  party-elector detail is deeper. Election-type outlier may confuse training.

## Two report-preamble families (matters for per-page labeling)
- MI SOVC tool + bedford: lead with a TURNOUT/PARTICIPATION SUMMARY page; the
  labeled candidate features are deeper -> excerpt page 1 mislabeled.
- Electionware (elk, adams) + green-banded (ionia): lead WITH candidate data
  (per-precinct block or straight-party grid) -> excerpt page 1 matches label.
=> Reinforces: labels should be PER-PAGE for image training, not per-file.

## GOAL (Mike, refined) — training-data quality
- Keep the chosen files and trust the manual labels; the task is fixing WHICH
  pages represent each file.
- Training data must rely LESS on Mike's personal vendor knowledge and MORE on
  self-contained CONTENT: actual CONTEST RESULTS pages (candidate/party names +
  the vote VALUES we ultimately want to extract), not turnout/summary preambles
  or cover sheets.
- Go UPSTREAM to the full originals (URLs in seed_sources.tsv) and use repo
  tools (render, page_table, page_words, inspect_page) to locate the right pages.
- REQUIREMENT: include good multi-PAGE SEQUENCE examples. A results table can
  split over multiple pages along BOTH axes — more precinct ROWS down and more
  candidate COLUMNS across — and the CONTEST NAME may appear only on a PRIOR
  page. So the example unit is sometimes a contiguous RUN of pages, not one page;
  a lone page from mid-run is unlabelable/unusable on its own.

## SEGMENTER SPEC (validated on Barry full 394pp)
Decisions locked: commit contiguous re-excerpted PDFs + a manifest; tool
proposes segments, Mike confirms per file; first scope = preamble-first files
(MI SOVC cluster + bedford); segment = BOUNDED WINDOW (~4pp) showing both splits,
values intentionally incomplete.

Content signals (vector PDFs, from page_words):
- summary page: contains "Registered Voters"/"Voters Cast"/"% Turnout", no
  "Vote For", no office name. (Barry p1-7.)
- contest-start (results) page: contains "(Vote for N)" and/or an office name;
  carries the contest TITLE. (Barry p8 Straight Party, p22 President, ...)
- continuation page: no "Vote For", has many integer cells; belongs to the
  contest that started on a prior page.
- rotated headers: candidate/party header text is read REVERSED by pdfplumber
  (e.g. "ytraP"=Party, "sirraH"=Harris) -> content-derived rotated-header signal.
- cover page: few tokens, ~no integers.

Contest geometry:
- WIDTH = number of candidate-column pages per precinct block = offset at which
  the first candidate column header (page's tokens after "Precinct") REPEATS.
  Barry President width=3 (p22-24 same precincts across DEM/REP -> LIB/... ->
  write-ins/Total), then p25 restarts columns with the NEXT precincts.
- Bounded window = [start .. start+WIDTH]: contest-name page + all column
  continuations of block 1 + first page of block 2 (the row continuation).
  Barry President -> p22-25. Roles: p22 results, p23-24 continuation-columns,
  p25 continuation-rows.

Scanned PDFs (gogebic, benzie-scanned): page_words is EMPTY -> text signals fail;
need a vision (inspect_page) pass. Build vector path first, scanned second.
- Vendor clusters: (a) MI vector SOVC (barry, calhoun, ...) near-identical;
  (b) PA/[MI] per-precinct summary (elk, adams) near-identical. One exemplar per
  vendor likely covers the cluster.
- Same county published in MULTIPLE containers: gogebic, kalkaska, otsego,
  montcalm each appear as xlsx (labeled "plain") AND as scanned/vector PDF (in
  stitch cluster). Dedupe by county+container when sampling.
- Excerpting (4-page fixtures) repeatedly SEVERS real page sequences: page 4 is
  typically pulled from deep in the doc, so it is discontinuous with pages 1-3.
  Genuine multi-page stitch examples likely need UPSTREAM originals from GitHub.
- Label question: elk-pa flagged stitch, adams-pa NOT, though both are the same
  vendor with the same cross-page precinct blocks. Revisit stitch labeling.
  RESOLVED (Mike): Adams EXCLUDES write-ins, so each contest fits neatly on a
  single page and nothing spills over; Elk INCLUDES many write-ins, forcing the
  spill. So the stitch flag correctly reflects actual spill, driven by whether
  write-ins are present. => write-in presence is a real driver of page-spilling.

## gogebic-mi ... .xlsx (and its companion PDF)
- xlsx opened in Numbers AND LibreOffice: headers MANGLED or MISSING. Sheets are
  broken into precinct rows but you CANNOT tell which race is which (no usable
  contest headers). => this xlsx is effectively unlabelable from the spreadsheet
  alone; the header row didn't survive.
- The companion scanned PDF shows the SAME structure as Calhoun & Barry (MI SOVC
  precinct-row grid).
- Scan quality is POOR and quite TILTED -> will be a challenge for Textract later.
  => second confirmed tilt/skew case (after benzie). Skew is a recurring scanned
     property to track.
- Implication: for these MI SOVC counties the xlsx is the DEGRADED copy (broken
  headers); the scanned PDF carries the real structure. Prefer the PDF as the
  training exemplar for this county, not the xlsx.

## Re-excerpt progress + SOVC variants
Done (upstream -> bounded window, fixture overwritten, label checked, manifest):
- barry: President p22-25. BOTH-axes split (portrait, 3 candidate-column pages
  wide). Column-continuation pages LOSE the contest name. side_by_side->false.
- calhoun: President p17-18. ROW-ONLY split (landscape, all candidates fit one
  page width, width=1). Contest title AND headers REPEAT on every page, so each
  page is self-contained for identity; only precincts continue. side_by_side->false.

=> Same SOVC vendor produces TWO variants by page orientation: portrait forces a
   candidate-column split (both axes; name only on first page); landscape fits
   all candidates (row-only stitch; name repeats). Keep both as distinct examples.
=> The throwaway segtool heuristic is unreliable on this vendor (two "Precinct"
   tokens per row; '(Vote for' repeats every page in landscape variant). Use it
   as a hint only and confirm windows by eye.
=> has_side_by_side means >=2 CONTESTS side by side; a single-contest window is
   always false here even with many candidate columns. Mike ruled false (Barry),
   applied to calhoun too.
