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
    position, not by label. The expected-candidate list MAY BE INCOMPLETE (a real-world roster names
    the major candidates but can omit minor-party or independent ones); capture EVERY candidate row
    the contest actually has, not just the listed ones. For a row that MATCHES a listed candidate,
    return the matched EXPECTED name and party. For a candidate row that matches NO listed candidate,
    return the candidate's OWN name only, cleaned the same way a listed match is -- drop a running mate
    (any text after a "/" or "and") and drop any party code shown as a prefix or suffix -- with a blank
    party. A row like this in the MAIN candidate block is a legitimate candidate the list omitted --
    NOT a write-in. But a NAMED row the page places in a WRITE-IN SECTION is a write-in, not a
    candidate (see Write-in rows). Do NOT read party off the document. Exclude the statistics
    block (Registered Voters, Ballots Cast) and grand-total rows (e.g. "Total Votes Cast", "Contest
    Totals").

    Non-candidate rows. A row that is not a candidate at all is a write-in or a vote-integrity line;
    give it a blank party.
    - Vote-integrity rows: use the canonical single-word spelling "Overvotes" or "Undervotes" even
      when the grid splits or punctuates the label ("Ov | ervotes:" -> "Overvotes"); rejoin split
      words and drop trailing punctuation.
    - Write-in rows: the DOCUMENT'S STRUCTURE decides write-in status, NOT the expected list. A row is
      a write-in when the page marks it as one -- under a "Write-in" / "Qualified Write-In Candidates"
      heading, carrying a "Write-in:" label, or sitting in the trailing block of names (frequently all
      zero) that follows the ballot candidates. Set write_in=true on EVERY entry in that write-in
      section EVEN WHEN IT NAMES A REAL PERSON (e.g. a slate of qualified write-in candidates like
      "Brian Carroll", "Jesse Ventura"), plus any unresolved/scattered write-in or write-in total
      ("Qualified Write In", "Unresolved Write-In", "Write-In Totals", "Not Assigned", etc.). They are
      consolidated into ONE write-in row downstream, so flag them all. A minor-party candidate in the
      MAIN candidate block (especially one carrying a ballot party) is NOT a write-in. Additionally set
      write_in_total=true ONLY on a row that is an explicit AGGREGATE write-in total (labeled like
      "Write-In Totals" / "Total Write-Ins") -- NOT on a bare scattered "Write-in" row and NOT on a
      named qualified write-in candidate; those are components. When a real total row is present it is
      used, otherwise the components are summed.

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

    The expected-candidate list is an external aid that MAY BE INCOMPLETE -- a real-world roster names
    the major candidates but can omit minor-party or independent ones who are nonetheless on THIS
    page. So do NOT treat the list as the full set of candidates: identify EVERY candidate column the
    page actually has, however many, not just the ones on the list. For a column that MATCHES a listed
    candidate (headers may show a running mate, party, or garbled/reversed fragments -- match on the
    recognizable name) return that candidate's name and party EXACTLY as supplied. For a candidate
    column that matches NO listed candidate, return the candidate's OWN name only, cleaned the same
    way a listed match is -- drop a running mate (any text after a "/" or "and": "Cornel West / Melina
    Abdullah" -> "Cornel West") and drop any party code shown as a prefix or suffix ("NPA Cornel West"
    -> "Cornel West", "Randall Terry ... - UST" -> "Randall Terry") -- with a blank party. Such a
    column is a legitimate candidate the list simply omitted, NOT automatically a write-in. Do NOT read
    the party off the document. Set write_in=true on ANY write-in column (a
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
