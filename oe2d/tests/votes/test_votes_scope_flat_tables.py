'''Tests for scope_flat_tables (hermetic: no Textract, no LM, no source files).

scope_flat_tables is the pure core of the scanned/flat read path -- given the tables a page reader
already produced and a schema resolver, it scopes them to one contest by column structure and moves
the digits into votes + printed county totals. Both impure dependencies are injected, so a stub
schema fully drives it and a few tiny hand-built grids stand in for what Textract returns -- each
grid shaped to isolate one behaviour (a clean read, column-count scoping, write-in consolidation, a
page-straddling precinct). These are the passing guardrails; the Columbia multi-page column-count
case (the one that does NOT read today) gets its own failing test alongside them.
'''
import pytest

from ... import votes
from ...votes import signatures


def _col(index, candidate, party='', write_in=False, write_in_total=False):
    return signatures.ColumnRole(index=index, role='candidate', candidate=candidate, party=party,
                                 write_in=write_in, write_in_total=write_in_total)


def _schema(columns, label_column=0, skip_labels=()):
    return signatures.PageSchema(first_data_row=0, label_column=label_column, columns=list(columns),
                                 method_labels={}, skip_labels=list(skip_labels))


def _run(tables, schema, context='Harris (DEM)\nTrump (REP)'):
    return votes.scope_flat_tables(tables, context, lambda _anchor: schema)


def test_flat_read_gives_votes_totals_and_reconciles():
    # header row (no counts, skipped), two precincts, a county Total row -> checksum target
    grid = [
        ['', 'Harris', 'Trump'],
        ['Precinct 1', '10', '20'],
        ['Precinct 2', '30', '40'],
        ['Total', '40', '60'],
    ]
    schema = _schema([_col(1, 'Harris', 'DEM'), _col(2, 'Trump', 'REP')])
    vote_map, totals = _run([grid], schema)
    assert vote_map[('Precinct 1', 'Harris', 'DEM')] == {'votes': 10}
    assert vote_map[('Precinct 2', 'Trump', 'REP')] == {'votes': 40}
    assert totals == {('Harris', 'DEM'): 40, ('Trump', 'REP'): 60}
    assert votes._reconciles(vote_map, totals) is True     # 10+30==40, 20+40==60


def test_scoping_drops_a_table_of_a_different_column_count():
    # the contest's 3-col table plus a 2-col neighbour (another contest on the same page); scoping
    # by the anchor's column count must keep only the 3-col table, so 'Ghost' never becomes a precinct
    contest = [['', 'Harris', 'Trump'], ['Precinct 1', '10', '20']]
    neighbour = [['Other', 'X'], ['Ghost', '99']]
    schema = _schema([_col(1, 'Harris', 'DEM'), _col(2, 'Trump', 'REP')])
    vote_map, _totals = _run([contest, neighbour], schema)
    assert {precinct for (precinct, _c, _p) in vote_map} == {'Precinct 1'}


def test_write_in_column_folds_into_one_write_ins_row():
    # a column the schema marks write_in is consolidated into the single Write-ins row, not emitted
    # as a named candidate; and it is excluded from the reconciliation totals
    grid = [
        ['', 'Harris', 'Trump', 'Scattered'],
        ['Precinct 1', '10', '20', '3'],
        ['Total', '10', '20', '3'],
    ]
    schema = _schema([_col(1, 'Harris', 'DEM'), _col(2, 'Trump', 'REP'), _col(3, 'Scattered', write_in=True)])
    vote_map, totals = _run([grid], schema)
    assert vote_map[('Precinct 1', votes.WRITE_IN_LABEL, '')] == {'votes': 3}
    assert not any(candidate == 'Scattered' for (_p, candidate, _party) in vote_map)
    assert ('Scattered', '') not in totals                 # write-in kept out of the checksum


def test_label_only_row_continues_the_previous_precinct_across_tables():
    # a precinct whose row straddles a page: its data sits under a truncated label at the bottom of
    # one table, the rest of the name is a label-only row atop the next table (same column count)
    page_a = [['', 'Harris', 'Trump'], ['Big Creek Township,', '10', '20']]
    page_b = [['Precinct 1', '', ''], ['Sherman Township', '30', '40']]
    schema = _schema([_col(1, 'Harris', 'DEM'), _col(2, 'Trump', 'REP')])
    vote_map, _totals = _run([page_a, page_b], schema)
    precincts = {precinct for (precinct, _c, _p) in vote_map}
    assert precincts == {'Big Creek Township, Precinct 1', 'Sherman Township'}


def test_anchor_is_chosen_by_header_match_not_position_or_size():
    # the header-match anchor pick has to DISCRIMINATE: a decoy table from another contest comes
    # first and has more rows, but does not match the expected candidates; the target table comes
    # second and is a different width. Only if header_match (not position or row count) picks the
    # target does its column count win -- so the wider decoy is dropped and its wards never appear.
    decoy = [['', 'SMITH', 'JONES', 'DOE', 'Write-in'],
             ['WARD A', '5', '6', '7', '0'], ['WARD B', '5', '6', '7', '0'],
             ['WARD C', '5', '6', '7', '0']]
    target = [['', 'CASEY', 'MCCORMICK', 'Write-in'], ['BEAVER TWP', '125', '428', '0']]
    schema = _schema([_col(1, 'Casey', 'DEM'), _col(2, 'McCormick', 'REP'),
                      _col(3, 'Scattered', write_in=True)])
    vote_map, _totals = _run([decoy, target], schema, context='Casey (DEM)\nMcCormick (REP)')
    assert {precinct for (precinct, _c, _p) in vote_map} == {'BEAVER TWP'}


def test_label_only_row_after_data_does_not_stitch():
    # the straddle stitch only fires for a label-only row BEFORE any data in its table (a page-top
    # continuation). A stray label-only row that appears AFTER a data row is not a continuation, so
    # the `started` guard leaves it out -- it must not glue onto the preceding precinct's name.
    grid = [
        ['', 'Harris', 'Trump'],
        ['Precinct 1', '10', '20'],
        ['Stray Label', '', ''],                       # label-only, but after data -> ignored
        ['Precinct 2', '30', '40'],
    ]
    schema = _schema([_col(1, 'Harris', 'DEM'), _col(2, 'Trump', 'REP')])
    vote_map, _totals = _run([grid], schema)
    assert {precinct for (precinct, _c, _p) in vote_map} == {'Precinct 1', 'Precinct 2'}


def test_total_row_detected_by_empty_label():
    # a county-total row can arrive with a BLANK label (the vendor drops the word "Total"); a
    # data-bearing row with no label is a checksum total, captured into totals, not a precinct
    grid = [['', 'Harris', 'Trump'], ['Precinct 1', '10', '20'], ['', '10', '20']]
    schema = _schema([_col(1, 'Harris', 'DEM'), _col(2, 'Trump', 'REP')])
    vote_map, totals = _run([grid], schema)
    assert {precinct for (precinct, _c, _p) in vote_map} == {'Precinct 1'}
    assert totals == {('Harris', 'DEM'): 10, ('Trump', 'REP'): 20}


def test_total_row_detected_by_skip_label():
    # a row whose label the interpreter marked a skip label (e.g. "County Total") is a checksum
    # total, not a precinct -- captured into totals and kept out of the precinct rows
    grid = [['', 'Harris', 'Trump'], ['Precinct 1', '10', '20'], ['County Total', '10', '20']]
    schema = _schema([_col(1, 'Harris', 'DEM'), _col(2, 'Trump', 'REP')], skip_labels=['County Total'])
    vote_map, totals = _run([grid], schema)
    assert {precinct for (precinct, _c, _p) in vote_map} == {'Precinct 1'}
    assert totals == {('Harris', 'DEM'): 10, ('Trump', 'REP'): 20}


def test_empty_tables_returns_empty_without_resolving_a_schema():
    # no tables -> no votes, no totals, and the schema resolver is never called (nothing to anchor)
    called: list = []
    vote_map, totals = votes.scope_flat_tables([], 'Casey (DEM)', lambda anchor: called.append(anchor))
    assert vote_map == {} and totals == {}
    assert not called


@pytest.mark.xfail(strict=True, reason='multi-page column-count inconsistency: the continuation '
                   'page is dropped by single-anchor column-count scoping (see votes-HANDOFF-3). '
                   'Remove this marker when per-table interpretation lands.')
def test_same_contest_different_column_count_per_page():
    # Columbia US Senate shape: the anchor page merges each candidate's count and percent into one
    # cell ('428 76.16%'), so it comes back N columns wide; a later page of the SAME contest splits
    # some percents into their own cell ('425' | '76.58%'), coming back WIDER -- and with too few
    # rows for the percent-column normalizer (>= 3) to collapse it back. Single-anchor scoping keeps
    # only the anchor's column count, so the wider continuation page is dropped: its precinct AND the
    # county Total row (the reconcile checksum) vanish. The fix is to interpret each header-bearing
    # table on its own, reusing a schema across matching continuations rather than one global width.
    anchor = [
        ['', 'CASEY', 'MCCORMICK', 'Write-in'],
        ['BEAVER TWP', '125', '428 76.16%', '0'],
        ['BENTON TWP', '189', '569 73.61%', '0'],
    ]
    continuation = [                                    # percent split out -> 6 cols; only 2 data rows
        ['', 'CASEY', 'MCCORMICK', '', 'Write-in', ''],
        ['SUGARLOAF TWP', '119', '425', '76.58%', '0', ''],
        ['Total', '10962', '20600', '63.57%', '29', ''],
    ]
    schema = _schema([_col(1, 'Casey', 'DEM'), _col(2, 'McCormick', 'REP'),
                      _col(3, 'Scattered', write_in=True)])
    vote_map, totals = _run([anchor, continuation], schema, context='Casey (DEM)\nMcCormick (REP)')
    precincts = {precinct for (precinct, _c, _p) in vote_map}
    assert 'SUGARLOAF TWP' in precincts                 # the continuation page's precinct is kept
    assert totals                                        # its Total row is captured as the checksum
    assert votes._reconciles(vote_map, totals)
