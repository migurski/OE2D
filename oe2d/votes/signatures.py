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
    keeps its observed label verbatim with a blank party.

    Return ONLY structure -- never read or return a vote number.
    '''
    office: str = dspy.InputField()
    candidate_context: str = dspy.InputField(
        desc='the expected candidates, one per line as "Name (PARTY)" or just "Name" when no party '
             'applies; match observed columns to these and echo the supplied name+party')
    grid: str = dspy.InputField(
        desc='raw cells; one row per line as "<rownum>: cell0 | cell1 | ..." with 0-based columns')
    page_schema: PageSchema = dspy.OutputField()
