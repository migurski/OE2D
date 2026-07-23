# Fixture review notes (Mike's observations)

Purpose: characterize how multi-page contest grids continue, to define training
examples. To be turned into data later.

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
