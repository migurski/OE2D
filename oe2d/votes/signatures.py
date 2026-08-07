'''DSPy signatures for the vote-table interpreters.

An interpreter reads ONE page's raw grid and returns its STRUCTURE -- never a vote number.
A generic walker (oe2d.votes.walk_page / extract_precinct_contest) then follows the returned
indices and labels, so all of the document's English (candidate headers, method labels,
total/header rows) is decided by the LLM here and none is hard-coded in Python. Candidate columns
are matched against an externally supplied expected-candidate list (from oe2d.contests) and echo the
SUPPLIED name and party, which is what lets a caller override noisy source headers (running mates,
rotated text, a stray party like NPA) with the intended output.

Design note for optimization: all how-to-decide guidance lives in the Signature DOCSTRINGS, which a
prompt optimizer (GEPA) can evolve. The pydantic Field descriptions on the nested output models are
rendered into the prompt's output-format spec but are NOT reachable by the optimizer, so they state
only what each field structurally IS -- never edge-case reasoning. Keep it that way: new guidance
goes in the docstring, not the Field.
'''
from __future__ import annotations

import dspy
import pydantic


class ColumnRole(pydantic.BaseModel):
    '''How to read one data column of the grid.'''
    index: int = pydantic.Field(description='0-based column index in the grid')
    role: str = pydantic.Field(description='candidate | pseudo_office | total_votes | spacer')
    candidate: str = pydantic.Field(default='', description='candidate name (per the instructions); empty for non-candidate roles')
    party: str = pydantic.Field(default='', description='candidate party; empty if none or unmatched')
    write_in: bool = pydantic.Field(default=False, description='whether this column is a write-in')
    write_in_total: bool = pydantic.Field(default=False, description='whether this write-in column is an explicit aggregate total')


class PageSchema(pydantic.BaseModel):
    '''The structural interpretation of one contest-major results page.'''
    first_data_row: int = pydantic.Field(description="0-based row index where the first precinct's block begins")
    label_column: int = pydantic.Field(description='index of the column holding precinct names and method-row labels')
    columns: list[ColumnRole] = pydantic.Field(description='one entry per DATA column that carries values')
    method_labels: dict[str, str] = pydantic.Field(description='row label -> canonical bucket: election_day, early_voting, absentee_mail, provisional, or total')
    skip_labels: list[str] = pydantic.Field(description='row labels to skip (totals / section headers)')


class CandidateRow(pydantic.BaseModel):
    '''One candidate's row in a precinct-major (candidates-as-rows) table.'''
    row_index: int = pydantic.Field(description='0-based grid row of this row within this contest')
    candidate: str = pydantic.Field(description='candidate name, or the label for a write-in / vote-integrity row')
    party: str = pydantic.Field(default='', description='candidate party; empty if unmatched')
    write_in: bool = pydantic.Field(default=False, description='whether this row is a write-in')
    write_in_total: bool = pydantic.Field(default=False, description='whether this write-in row is an explicit aggregate total')


class PrecinctPageSchema(pydantic.BaseModel):
    '''Structure of a precinct-major page (one precinct per page, contests stacked, candidates
    down the rows, vote methods across the columns). Learned once from a sample page and applied
    to every structurally-identical page in the document.'''
    precinct_row: int = pydantic.Field(description='row index whose label cell holds the PRECINCT name (the page title)')
    precinct_column: int = pydantic.Field(description='column index of the precinct-name cell (usually 0)')
    method_columns: dict[int, str] = pydantic.Field(description='column index -> canonical bucket (election_day, early_voting, absentee_mail, provisional, or total)')
    candidate_rows: list[CandidateRow] = pydantic.Field(description="this contest's choice rows")


class InterpretPrecinctPage(dspy.Signature):
    '''Interpret ONE precinct-major results page into a reusable structural schema.

    The page is a single precinct (its name is the page title); contests are stacked down the page,
    candidates run DOWN the rows of a contest, and the vote methods (Total, Election Day, Mail,
    Provisional) run ACROSS the columns. There is also a statistics block (Registered Voters,
    Ballots Cast) that is NOT part of any contest.

    For the given office return: which row/column holds the precinct name (precinct_row is the page
    title, precinct_column usually 0); which columns are which vote method; and the contest's
    candidate rows.

    Candidate rows. Give each row's row_index (0-based grid row) WITHIN THIS CONTEST -- when several
    contests stack on the page their write-in / over-vote / under-vote labels repeat, so scope by
    position, not by label. For a row that matches an expected candidate, return the matched EXPECTED
    name and party. Do NOT read party off the document. Exclude the statistics block (Registered
    Voters, Ballots Cast) and grand-total rows (e.g. "Total Votes Cast", "Contest Totals").

    Non-candidate rows. A row that matches no expected candidate is a write-in or a vote-integrity
    line; give it a blank party.
    - Vote-integrity rows: use the canonical single-word spelling "Overvotes" or "Undervotes" even
      when the grid splits or punctuates the label ("Ov | ervotes:" -> "Overvotes"); rejoin split
      words and drop trailing punctuation.
    - Write-in rows: set write_in=true on ANY write-in (a named/qualified write-in, an
      unresolved/scattered write-in, or a write-in total -- "Qualified Write In", "Unresolved
      Write-In", "Write-In Totals", "Not Assigned", etc.); they are consolidated into one write-in
      row downstream, so flag them all. Additionally set write_in_total=true ONLY on a row that is an
      explicit AGGREGATE write-in total (labeled like "Write-In Totals" / "Total Write-Ins") -- NOT
      on a bare scattered "Write-in" row and NOT on a named qualified write-in candidate; those are
      components. When a real total row is present it is used, otherwise the components are summed.

    Return ONLY structure -- never read or return a vote number.
    '''
    office: str = dspy.InputField()
    electoral_context: str = dspy.InputField(desc='the expected candidates, one per line as "Name (PARTY)" or just "Name"')
    grid: str = dspy.InputField(desc='raw cells of ONE sample page; one row per line as "<rownum>: cell0 | cell1 | ..."')
    precinct_schema: PrecinctPageSchema = dspy.OutputField()


class InterpretResultsPage(dspy.Signature):
    '''Interpret ONE page of a contest-major precinct election-results grid into a structural schema.

    You get the raw extracted cells for one page of a single contest's results, the office, and a
    list of EXPECTED candidates supplied from outside. Precincts run down the rows; candidates are
    columns. Each precinct is a label row (sometimes wrapping onto a second row) followed by
    vote-method rows. Some columns are candidates, others are a pseudo-office like "Registered
    Voters", a cross-candidate "Total Votes" column, or an empty spacer.

    first_data_row is the 0-based index of the FIRST row of the first precinct's block -- its
    precinct-label row, NOT the first vote-method row; ignore the rows above it (title banner, column
    headers). label_column is the column holding precinct names and method-row labels. In method_labels
    map each vote-method row label to its canonical bucket (election_day, early_voting, absentee_mail,
    provisional, or total). In skip_labels list the row labels that are totals or section headers to
    skip (a county grand-total row, a cumulative section, a "County" header).

    For each candidate column, MATCH its header to one of the expected candidates (headers may show a
    running mate, party, or garbled/reversed fragments -- match on the recognizable name) and return
    that expected candidate's name and party EXACTLY as supplied. Do NOT read the party off the
    document. A candidate column that matches no expected candidate (typically a write-in line) keeps
    its observed label verbatim with a blank party. Set write_in=true on ANY write-in column (a
    named/qualified write-in, an unresolved/scattered write-in, or a write-in total -- "Qualified
    Write In", "Unresolved Write-In", "Write-In Totals", "Not Assigned", etc.); they are consolidated
    into one write-in row downstream, so flag them all. Additionally set write_in_total=true ONLY on a
    column that is an explicit AGGREGATE write-in total (labeled like "Write-In Totals" / "Total
    Write-Ins") -- NOT on a bare scattered "Write-in" column and NOT on a named qualified write-in
    candidate; those are components. A real total column is used when present, otherwise the
    components are summed (so a scattered "Write-in" line and the named qualified write-ins are added
    together).

    Return ONLY structure -- never read or return a vote number.
    '''
    office: str = dspy.InputField()
    electoral_context: str = dspy.InputField(
        desc='the expected candidates, one per line as "Name (PARTY)" or just "Name" when no party '
             'applies; match observed columns to these and echo the supplied name+party')
    grid: str = dspy.InputField(
        desc='raw cells; one row per line as "<rownum>: cell0 | cell1 | ..." with 0-based columns')
    page_schema: PageSchema = dspy.OutputField()
