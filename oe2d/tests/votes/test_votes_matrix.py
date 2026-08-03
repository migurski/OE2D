'''Tests for read_matrix_page (hermetic: no Textract, no LM, no source files).

A precinct-MATRIX page (an Alameda-style SOVC) lists precincts down the rows but keeps the precinct
id and the vote-method label in SEPARATE columns -- each data row is one precinct x one method (the id
repeating down its methods), candidates in columns. The LANGUAGE (candidate columns, and the method
label -> canonical bucket map) is the shared interpreter's PageSchema, injected here as a stub; the
read detects the separate precinct-id column deterministically and groups the method rows under it.
These pin that: the per-precinct Total becomes votes, the method rows fill the breakdown buckets, a
privacy-suppressed ("***") precinct is kept with BLANK (None) values -- present, not zero, not absent
-- and the "Contest Total" row is captured as the checksum.
'''
from ... import votes
from ...votes import signatures


def _col(index, candidate, party):
    return signatures.ColumnRole(index=index, role='candidate', candidate=candidate, party=party)


# columns: 0 precinct-id, 1 method label, 2 Registered Voters (stat), 6 REP Chen, 7 DEM Khanna.
SCHEMA = signatures.PageSchema(
    first_data_row=2,
    label_column=1,                                   # the vote-method column
    columns=[
        signatures.ColumnRole(index=2, role='pseudo_office', candidate='', party=''),
        _col(6, 'Anita Chen', 'REP'),
        _col(7, 'Ro Khanna', 'DEM'),
    ],
    method_labels={'Election Day': 'election_day', 'Vote by Mail': 'absentee_mail', 'Total': 'total'},
    skip_labels=[],
)
GRID = [
    ['Office', 'Office', 'Office', 'Office', '', 'Office', 'Office', 'Office'],       # r0 title banner
    ['', '', 'Registered Voters', '', '', '', 'REP - ANITA CHEN', 'DEM - RO KHANNA'], # r1 column header
    ['A', 'Election Day', '100', '', '', '', '10', '20'],
    ['A', 'Vote by Mail', '100', '', '', '', '30', '40'],
    ['A', 'Total', '100', '', '', '', '40', '60'],
    ['B', 'Election Day', '80', '', '', '', '5', '7'],
    ['B', 'Vote by Mail', '80', '', '', '', '15', '23'],
    ['B', 'Total', '80', '', '', '', '20', '30'],
    ['C', 'Election Day', '1', '', '', '', '***', '***'],       # a privacy-masked precinct
    ['C', 'Total', '1', '', '', '', '***', '***'],
    ['Contest Total', '', '181', '', '', '', '60', '90'],       # the grand total -> reconcile target
]


def test_matrix_total_becomes_votes_with_method_breakdown() -> None:
    votes_out, _totals = votes.read_matrix_page(GRID, SCHEMA)
    assert votes_out[('A', 'Anita Chen', 'REP')] == {'votes': 40, 'election_day': 10, 'absentee_mail': 30}
    assert votes_out[('A', 'Ro Khanna', 'DEM')] == {'votes': 60, 'election_day': 20, 'absentee_mail': 40}
    assert votes_out[('B', 'Ro Khanna', 'DEM')] == {'votes': 30, 'election_day': 7, 'absentee_mail': 23}


def test_suppressed_precinct_kept_with_blanks() -> None:
    # C's cells are non-numeric ("***"): the precinct is present but its values are withheld, so it is
    # kept with None (blank) buckets -- distinct from an absent cell (skipped) and a zero.
    votes_out, _totals = votes.read_matrix_page(GRID, SCHEMA)
    assert votes_out[('C', 'Anita Chen', 'REP')] == {'election_day': None, 'votes': None}
    # and it renders as blank cells that survive the roster (drop_all_zero off for the matrix path)
    rows = votes.votes_to_rows(votes_out, 'Alameda', 'U.S. House', '17', drop_all_zero=False)
    c_chen = next(r for r in rows if r['precinct'] == 'C' and r['party'] == 'REP')
    assert c_chen['votes'] == '' and c_chen['election_day'] == ''


def test_contest_total_is_captured_and_reconciles() -> None:
    votes_out, totals = votes.read_matrix_page(GRID, SCHEMA)
    assert totals == {('Anita Chen', 'REP'): 60, ('Ro Khanna', 'DEM'): 90}
    # the two disclosed precincts sum to the grand total (no masked votes in this fixture)
    assert votes._reconciles(votes_out, totals, strict=True) is True


def test_precinct_column_detected_not_the_stat_column() -> None:
    # col 0 (unlisted, empty header, data) is the precinct id -- NOT col 2 (Registered Voters, listed);
    # precinct ids A/B/C are read from col 0, not the numeric stat column.
    votes_out, _totals = votes.read_matrix_page(GRID, SCHEMA)
    assert {precinct for precinct, _c, _p in votes_out} == {'A', 'B', 'C'}


# The dispatch probe: a MATRIX page has a dedicated vote-method column (a small label set cycling once
# per precinct); a walk_page columns page interleaves the method labels INTO the precinct-label column,
# which then carries every precinct name (high cardinality) -- no dedicated method column.
def test_looks_like_matrix_true_for_dedicated_method_column() -> None:
    # 8 precincts x (Election Day / Vote by Mail / Total) -- col 1 is 3 labels cycling 8 times.
    grid = [['Office', 'Office', 'REP Chen', 'DEM Khanna']]
    for p in range(8):
        pid = str(830000 + p)
        for method, a, b in (('Election Day', '10', '20'), ('Vote by Mail', '30', '40'),
                             ('Total', '40', '60')):
            grid.append([pid, method, a, b])
    assert votes._looks_like_matrix(grid) is True


def test_looks_like_matrix_false_for_walk_page_layout() -> None:
    # walk_page shape: col 0 is a precinct-label row then method rows, so it carries every precinct
    # NAME plus the methods -- high cardinality, no small cycling label set standing alone.
    grid = [['Office', 'Registered', 'Chen', 'Khanna']]
    for name in ('Alpha Township', 'Bravo Township', 'Charlie Township', 'Delta Township',
                 'Echo Township', 'Foxtrot Township', 'Golf Township', 'Hotel Township'):
        grid.append([name, '', '', ''])
        grid.append(['Election Day', '100', '40', '50'])
        grid.append(['Total', '100', '40', '50'])
    assert votes._looks_like_matrix(grid) is False
