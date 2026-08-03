'''Tests for read_matrix_page (hermetic: no Textract, no LM, no source files).

A precinct-MATRIX page (an Alameda-style SOVC) lists precincts down the rows but keeps the precinct
id and the vote-method label in SEPARATE columns -- each data row is one precinct x one method (the id
repeating down its methods), candidates in columns. The LANGUAGE (candidate columns, and the method
label -> canonical bucket map) is the shared interpreter's PageSchema, injected here as a stub; the
read detects the separate precinct-id column deterministically and groups the method rows under it.
These pin that: the per-precinct Total becomes votes, the method rows fill the breakdown buckets, a
privacy-masked ("***") precinct drops out, and the "Contest Total" row is captured as the checksum.
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


def test_masked_precinct_drops_out() -> None:
    votes_out, _totals = votes.read_matrix_page(GRID, SCHEMA)
    assert not any(precinct == 'C' for precinct, _c, _p in votes_out)


def test_contest_total_is_captured_and_reconciles() -> None:
    votes_out, totals = votes.read_matrix_page(GRID, SCHEMA)
    assert totals == {('Anita Chen', 'REP'): 60, ('Ro Khanna', 'DEM'): 90}
    # the two disclosed precincts sum to the grand total (no masked votes in this fixture)
    assert votes._reconciles(votes_out, totals, strict=True) is True


def test_precinct_column_detected_not_the_stat_column() -> None:
    # col 0 (unlisted, empty header, data) is the precinct id -- NOT col 2 (Registered Voters, listed)
    votes_out, _totals = votes.read_matrix_page(GRID, SCHEMA)
    assert {precinct for precinct, _c, _p in votes_out} == {'A', 'B'}
