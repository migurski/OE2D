'''Tests for the ruled_columns reconcile confirm (hermetic: no Textract, no LM, no source files).

A ruled_columns read is a method-sub-row SCAN (a ClearBallot SOVC: each precinct carries Election
Day / AV / Early Voting / Total sub-rows) read with Textract TABLES. Its faint-grid failure mode
(Gogebic) mis-segments ONLY ONE candidate column, so the majority reconcile that guards the flat
family would wave it through; ruled_columns must therefore confirm with a STRICT all-columns match and
drop to auto on any mismatch. _county_totals recovers the printed county total from the SOVC's
grand-total row (label carries both "county" and "total") to check against. These pin both.
'''
from ... import votes
from ...votes import signatures


def _col(index, candidate, party='', write_in=False):
    return signatures.ColumnRole(index=index, role='candidate', candidate=candidate, party=party,
                                 write_in=write_in)


def _schema(columns, label_column=0):
    return signatures.PageSchema(first_data_row=0, label_column=label_column, columns=list(columns),
                                 method_labels={}, skip_labels=[])


# One candidate-group page: label column 0, Harris col 1, Trump col 2, a write-in col 3, and a
# grand-total row whose label carries "county"+"total".
SCHEMA = _schema([_col(1, 'Harris', 'DEM'), _col(2, 'Trump', 'REP'), _col(3, 'Scattered', write_in=True)])
GRID = [
    ['Precinct', 'Harris', 'Trump', 'Write-in'],
    ['Alpha', '40', '50', '1'],
    ['Bravo', '60', '70', '2'],
    ['County Total', '100', '120', '3'],
]


def test_county_totals_reads_the_grand_total_row() -> None:
    totals = votes._county_totals([(SCHEMA, GRID)])
    # real candidates only; write-in column excluded (it consolidates downstream)
    assert totals == {('Harris', 'DEM'): 100, ('Trump', 'REP'): 120}


def test_county_totals_ignores_cumulative_total_row() -> None:
    # a zero "Cumulative Total" row (no "county") must NOT be mistaken for the grand total
    grid = GRID[:-1] + [['Cumulative Total', '0', '0', '0'], ['County Total', '100', '120', '3']]
    assert votes._county_totals([(SCHEMA, grid)]) == {('Harris', 'DEM'): 100, ('Trump', 'REP'): 120}


def test_strict_reconcile_requires_every_column() -> None:
    # three candidate columns, one precinct each, so the sums equal the printed totals exactly
    totals = {('Harris', 'DEM'): 40, ('Trump', 'REP'): 50, ('Oliver', 'LIB'): 6}
    good = {('Alpha', 'Harris', 'DEM'): {'votes': 40}, ('Alpha', 'Trump', 'REP'): {'votes': 50},
            ('Alpha', 'Oliver', 'LIB'): {'votes': 6}}
    assert votes._reconciles(good, totals, strict=True) is True
    # Gogebic's failure: ONE column (Oliver) mis-segmented -> 2 of 3 still match, so the majority test
    # (2*2 > 3) waves it through; strict must veto it so the read drops to auto.
    bad = dict(good)
    bad[('Alpha', 'Oliver', 'LIB')] = {'votes': 1}
    assert votes._reconciles(bad, totals, strict=False) is True     # 2/3 -> majority passes
    assert votes._reconciles(bad, totals, strict=True) is False     # strict vetoes the one mismatch


def test_strict_reconcile_without_totals_is_false() -> None:
    assert votes._reconciles({('Alpha', 'Harris', 'DEM'): {'votes': 40}}, {}, strict=True) is False
