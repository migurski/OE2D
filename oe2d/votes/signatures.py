'''DSPy signature for the vote-table interpreter.

The interpreter reads ONE page's raw grid and returns its STRUCTURE -- never a vote number.
A generic walker (oe2d.votes.walk_page) then follows the returned indices and labels, so all
of the document's English (candidate headers, method labels, total/header rows) is decided by
the LLM here and none is hard-coded in Python. Candidate columns are matched against an
externally supplied expected-candidate list (from oe2d.contests) and echo the SUPPLIED name and
party, which is what lets a caller override noisy source headers (running mates, rotated text,
a stray party like NPA) with the intended output.
'''
from __future__ import annotations

import dspy
import pydantic


class ColumnRole(pydantic.BaseModel):
    '''How to read one data column of the grid.'''
    index: int = pydantic.Field(description='0-based column index in the grid')
    role: str = pydantic.Field(description='candidate | pseudo_office | total_votes | spacer')
    candidate: str = pydantic.Field(
        default='',
        description='candidate role only: the matched EXPECTED candidate name verbatim; if the '
                    'column matches no expected candidate (e.g. a write-in line), the observed '
                    'label verbatim')
    party: str = pydantic.Field(
        default='',
        description='candidate role only: the party of the MATCHED expected candidate (blank if '
                    'the expected entry gave none); blank for an unmatched column. Do NOT read '
                    'party off the document header')
    write_in: bool = pydantic.Field(
        default=False,
        description='true if this candidate column is any kind of write-in -- a named/qualified '
                    'write-in candidate, an unresolved/scattered write-in, or a write-in total. '
                    'All write-in columns are later combined into one consolidated write-in row')
    write_in_total: bool = pydantic.Field(
        default=False,
        description='write-in columns only: true ONLY if this column is an explicit AGGREGATE '
                    'total of write-ins -- a column labeled like "Write-In Totals" / "Total '
                    'Write-Ins" that already sums the itemized write-ins. Leave FALSE for a '
                    'scattered/unresolved bare "Write-in" line and for a named qualified write-in '
                    'candidate (e.g. "Peter Sonski") -- those are components that get summed. When '
                    'a real total column exists it is used instead of summing the components')


class PageSchema(pydantic.BaseModel):
    '''The structural interpretation of one results page.'''
    first_data_row: int = pydantic.Field(
        description="0-based index of the FIRST row of the first precinct's block -- its "
                    'precinct-label row, NOT the first vote-method row. Rows above it (title '
                    'banner, column headers) are ignored')
    label_column: int = pydantic.Field(
        description='index of the column holding precinct names and method-row labels')
    columns: list[ColumnRole] = pydantic.Field(
        description='one entry per DATA column that carries values')
    method_labels: dict[str, str] = pydantic.Field(
        description='map each row label that denotes a vote-method breakdown to its canonical '
                    'bucket: election_day, early_voting, absentee_mail, provisional, or total '
                    '(the grand total for the row)')
    skip_labels: list[str] = pydantic.Field(
        description='row labels that are totals or section headers to skip (e.g. a county '
                    'grand-total row, a cumulative section, a "County" header)')


class CandidateRow(pydantic.BaseModel):
    '''One candidate's row in a precinct-major (candidates-as-rows) table.'''
    row_index: int = pydantic.Field(description='0-based grid row of this candidate WITHIN THIS CONTEST -- scopes to the right contest when several are stacked on the page (their write-in/over/under labels repeat)')
    candidate: str = pydantic.Field(description='matched EXPECTED candidate name; or the observed label verbatim for a write-in / vote-integrity row (Write-In Totals, Overvotes, ...)')
    party: str = pydantic.Field(default='', description='matched expected party; blank if unmatched. Do NOT read party off the document')
    write_in: bool = pydantic.Field(default=False, description='true if this row is any kind of write-in (named/qualified write-in, unresolved/scattered write-in, or write-in total); all write-in rows are combined into one consolidated write-in row')
    write_in_total: bool = pydantic.Field(default=False, description='write-in rows only: true ONLY if this row is an explicit AGGREGATE total of write-ins (labeled like "Write-In Totals" / "Total Write-Ins") that already sums the itemized write-ins. Leave FALSE for a scattered/unresolved bare "Write-in" row and for a named qualified write-in candidate -- those are components that get summed. A real total row is used instead of summing the components')


class PrecinctPageSchema(pydantic.BaseModel):
    '''Structure of a precinct-major page (one precinct per page, contests stacked, candidates
    down the rows, vote methods across the columns). Learned once from a sample page and applied
    to every structurally-identical page in the document.'''
    precinct_row: int = pydantic.Field(description='row index whose label cell holds the PRECINCT name (the page title, e.g. "Abbottstown")')
    precinct_column: int = pydantic.Field(description='column index of the precinct-name cell (usually 0)')
    method_columns: dict[int, str] = pydantic.Field(description='column index -> canonical bucket (election_day, early_voting, absentee_mail, provisional, or total)')
    candidate_rows: list[CandidateRow] = pydantic.Field(description="rows of THIS contest's choices (match office + expected candidates; exclude the statistics block and grand-total rows)")


class InterpretPrecinctPage(dspy.Signature):
    '''Interpret ONE precinct-major results page into a reusable structural schema.

    The page is a single precinct (its name is the page title); contests are stacked down the page,
    candidates run DOWN the rows of a contest, and the vote methods (Total, Election Day, Mail,
    Provisional) run ACROSS the columns. There is also a statistics block (Registered Voters,
    Ballots Cast) that is NOT part of any contest.

    For the given office, return: which row/column holds the precinct name; which columns are which
    vote method; and the contest's candidate rows -- each with its row-label verbatim (so the same
    rows can be found on every page) and the matched EXPECTED candidate name and party. A row that
    matches no expected candidate (a write-in or vote-integrity line) keeps its observed label
    verbatim with a blank party. Set write_in=true on ANY write-in row (a named/qualified write-in,
    an unresolved/scattered write-in, or a write-in total -- "Qualified Write In", "Unresolved
    Write-In", "Write-In Totals", "Not Assigned", etc.); they are consolidated into one write-in
    total downstream, so flag them all. Additionally set write_in_total=true ONLY on a row that is
    an explicit AGGREGATE write-in total (labeled like "Write-In Totals" / "Total Write-Ins") --
    NOT on a bare scattered "Write-in" row and NOT on a named qualified write-in candidate; those
    are components. When a real total row is present it is used, otherwise the components are
    summed. Exclude the statistics block and grand-total rows (e.g. "Total Votes Cast", "Contest
    Totals").

    Return ONLY structure -- never read or return a vote number.
    '''
    office: str = dspy.InputField()
    candidate_context: str = dspy.InputField(desc='the expected candidates, one per line as "Name (PARTY)" or just "Name"')
    grid: str = dspy.InputField(desc='raw cells of ONE sample page; one row per line as "<rownum>: cell0 | cell1 | ..."')
    precinct_schema: PrecinctPageSchema = dspy.OutputField()


class InterpretResultsPage(dspy.Signature):
    '''Interpret ONE page of a precinct election-results grid into a structural schema.

    You get the raw extracted cells for one page of a single contest's results, the office, and a
    list of EXPECTED candidates supplied from outside. Precincts run down the rows; candidates are
    columns. Each precinct is a label row (sometimes wrapping onto a second row) followed by
    vote-method rows. Some columns are candidates, others are a pseudo-office like "Registered
    Voters", a cross-candidate "Total Votes" column, or an empty spacer.

    For each candidate column, MATCH its header to one of the expected candidates (headers may show
    a running mate, party, or garbled/reversed fragments -- match on the recognizable name) and
    return that expected candidate's name and party EXACTLY as supplied. Do NOT read the party off
    the document. A candidate column that matches no expected candidate (typically a write-in line)
    keeps its observed label verbatim with a blank party. Set write_in=true on ANY write-in column
    (a named/qualified write-in, an unresolved/scattered write-in, or a write-in total -- "Qualified
    Write In", "Unresolved Write-In", "Write-In Totals", "Not Assigned", etc.); they are
    consolidated into one write-in total downstream, so flag them all. Additionally set
    write_in_total=true ONLY on a column that is an explicit AGGREGATE write-in total (labeled like
    "Write-In Totals" / "Total Write-Ins") -- NOT on a bare scattered "Write-in" column and NOT on a
    named qualified write-in candidate; those are components. A real total column is used when
    present, otherwise the components are summed (so a scattered "Write-in" line and the named
    qualified write-ins are added together).

    Return ONLY structure -- never read or return a vote number.
    '''
    office: str = dspy.InputField()
    candidate_context: str = dspy.InputField(
        desc='the expected candidates, one per line as "Name (PARTY)" or just "Name" when no party '
             'applies; match observed columns to these and echo the supplied name+party')
    grid: str = dspy.InputField(
        desc='raw cells; one row per line as "<rownum>: cell0 | cell1 | ..." with 0-based columns')
    page_schema: PageSchema = dspy.OutputField()
