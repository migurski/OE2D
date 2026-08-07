'''DSPy signature for the page analyzer, plus the label vocabularies its fields use.

The signature's docstring is the SEED instruction GEPA evolves; the optimized
instruction lives on the predictor and is serialized into the model artifact, so
editing here changes only the starting point, not a committed optimized program.
'''
from __future__ import annotations

import typing

import dspy


# Per-page label vocabularies as Literal types; the DSPy output fields (below) and the
# pydantic PageProperties result model both take their types from these, so the taxonomy
# has a single definition. Literal (not Enum) on purpose: DSPy's output parser is lenient
# for Literal -- it strips stray surrounding quotes a model may emit ('columns' -> columns)
# -- but STRICT for Enum (find_enum_member), which rejects that quoting and breaks parsing
# on models that format enum values with quotes (e.g. Llama 4 Scout). Literal keeps the
# analyzer parseable across vendors.
CandidateOrientation = typing.Literal['columns', 'rows']
PrecinctScope = typing.Literal['multi_precinct', 'per_precinct', 'county']
# The precinct axis is only meaningful for multi_precinct pages; 'none' covers per_precinct
# (one precinct, named in a header) and county (no precinct at all), so the field is always a
# concrete member rather than null.
PrecinctAxis = typing.Literal['rows', 'columns', 'none']

# The three READ-SHAPE observations. The seven fields above name a page's axes and what labels
# are present; these name the finer layout an extractor must route on -- distinct read strategies
# that share identical values for the older fields (a mega-grid and a single-contest table are
# both columns/multi_precinct/rows; three Dominion/Electionware rows layouts are all
# rows/per_precinct). Each is a plain visible fact, combined downstream in votes.detect_dispatch.
#
# contests_across: how many distinct contests run ACROSS the page columns side-by-side, sharing
# one precinct-row axis (a mega-grid) -- 'multiple' -- versus one contest spanning the columns
# ('single'). Named for the horizontal axis on purpose: contests STACKED down the page do not
# count, so "how many contests are on the page" would mislabel a stacked page as multiple.
ContestsAcross = typing.Literal['single', 'multiple']
# precinct_rows: on a multi_precinct page, does each precinct occupy ONE data row, or SEVERAL
# stacked vote-method sub-rows (Election Day / Absentee / Total, one per precinct)? 'none' when
# the page is not multi_precinct (per_precinct or county).
PrecinctRows = typing.Literal['single', 'multiple', 'none']
# value_columns: for ONE candidate/choice, how many number columns carry its vote figures -- a
# lone total, several method totals, or count+percent PAIRS per method? Separates the three
# rows-orientation report layouts and flags percent-bearing scans.
ValueColumns = typing.Literal['total_only', 'methods', 'methods_with_percent']


class PageAnalysis(dspy.Signature):
    '''Report factual, in-page observations about ONE election-results page image.

    You are shown a single page image, not a whole document. Describe only what is
    visible on THIS page; do not infer contests or precincts that would be on other
    pages.

    electoral_context: you may be given a passage of external context about this page or its
    contest. Read it as self-explanatory prose and use it ONLY to resolve ambiguity you
    cannot settle from the image alone. In particular, if it names the candidates or
    choices, locate them on the page to fix the CANDIDATE AXIS -- if they run DOWN THE
    ROWS the orientation is 'rows', if they HEAD THE COLUMNS it is 'columns'. The context
    is an aid, not ground truth about THIS page: still report every presence field
    (contest_name_present, candidate_names_present, headers_present) from what is
    ACTUALLY visible in the image -- never mark something present just because the
    context mentions it. When context is empty, judge from the image alone.

    ruled_table: report whether the results table is drawn as a GRID of ruling lines
    that box the cells -- vertical rules separating the number columns AND horizontal
    rules between rows. Answer True only for a full drawn grid, one a line-based table
    reader could segment cells from by following the borders. Answer False when the
    columns are held by whitespace/alignment or by only a shaded header band, even if
    a few horizontal separators appear between rows -- horizontal lines alone are not a
    grid and do not make it ruled.

    contests_across: answer 'multiple' when TWO OR MORE different contests (different
    offices -- e.g. a US House race AND a state senate race, each with its own candidate
    columns and its own Total-Votes header) sit SIDE BY SIDE across the page, all sharing
    ONE column of precinct labels down the left, so each precinct is a single row that
    runs left-to-right through every contest (a mega-grid). Answer 'single' when only one
    contest's candidates span the page (however many candidate columns it has), or when a
    second contest appears only STACKED BELOW the first (a separate block further down the
    page, not beside it). A turnout / registered-voters block beside one contest is not a
    second contest.

    precinct_rows: on a multi_precinct page, answer 'single' when each precinct is ONE
    row of numbers, 'multiple' when each precinct is a stack of vote-method sub-rows
    (e.g. an Election Day row, an Absentee/AV row, a Total row -- one group per
    precinct). Answer 'none' when the page is not laid out as many precincts (a single
    per-precinct page, or a county-summary page).

    value_columns: look at ONE candidate/choice and count the kinds of number that
    follow it. Answer 'total_only' for a single vote figure. Answer 'methods' for
    several plain vote counts broken out by method (Election Day, Absentee, Total ...)
    with NO percentages. Answer 'methods_with_percent' when each method figure is a
    count paired with a percent (e.g. "1,234  57.3%") -- the tell is a % sign beside
    the counts.
    '''
    image: dspy.Image = dspy.InputField(desc='A single rendered election-results page')
    electoral_context: str = dspy.InputField(
        desc='Optional external electoral context about this page or its contest (may be empty); '
             'self-explanatory prose. See the instructions for how to use it')
    candidate_orientation: CandidateOrientation = dspy.OutputField(
        desc="Decided by where the CANDIDATE/PARTY NAMES run, NOT by the method or percent "
             "columns: 'columns' when candidate names head the columns (precincts run down "
             "the rows); 'rows' when candidate names label the rows -- e.g. a single "
             "precinct's page listing each candidate on its own row with vote-method columns "
             "(Election Day / Vote by Mail / Total) across the top")
    contest_name_present: bool = dspy.OutputField(
        desc='Is a contest/office title visible on this page? A continuation page '
             'that just carries more candidate columns or more precinct rows often '
             'has none')
    candidate_names_present: bool = dspy.OutputField(
        desc='Are candidate or party names visible on this page? False on a bare '
             'data-only continuation page')
    headers_present: bool = dspy.OutputField(
        desc='Are column/row headers labeling the numbers present on this page?')
    precinct_scope: PrecinctScope = dspy.OutputField(
        desc="'multi_precinct' when the page lays out many precincts along an axis; "
             "'per_precinct' when the page is a single precinct named in a heading "
             "with its results below; 'county' when the page shows county-wide "
             "aggregates with no precinct dimension")
    precinct_orientation: PrecinctAxis = dspy.OutputField(
        desc="For a multi_precinct page, whether precincts are 'rows' or 'columns'; "
             "otherwise 'none'")
    ruled_table: bool = dspy.OutputField(
        desc='Whether the results table is drawn as a full grid of ruling lines '
             '(see the instructions for how to decide)')
    contests_across: ContestsAcross = dspy.OutputField(
        desc="'multiple' when several different contests sit side-by-side across the "
             "columns (a mega-grid, one precinct row spanning them all); 'single' for one "
             "contest across the page (a second contest STACKED below still counts as "
             "single)")
    precinct_rows: PrecinctRows = dspy.OutputField(
        desc="For a multi_precinct page, whether each precinct is 'single' (one data "
             "row) or 'multiple' (a stack of vote-method sub-rows); 'none' when the "
             "page is not multi_precinct")
    value_columns: ValueColumns = dspy.OutputField(
        desc="Per candidate/choice, the number columns: 'total_only' (a lone total), "
             "'methods' (several method counts, no percentages), or "
             "'methods_with_percent' (each method a count+percent pair)")
