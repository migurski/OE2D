'''Tests for the schema-driven walker and stitch (hermetic: no LM, no source files).

These exercise the deterministic half of oe2d.votes -- given a PageSchema (what the interpreter
would return), the walker must pull the right cells and the stitch must reassemble precincts. The
walker holds no English of its own, so a stub schema fully drives it.
'''
from ... import votes
from ...votes import signatures


def _schema(**kwargs):
    base = dict(first_data_row=0, label_column=0, columns=[], method_labels={}, skip_labels=[])
    base.update(kwargs)
    return signatures.PageSchema(**base)


def test_walk_page_wrapped_label_and_methods():
    # a precinct whose name wraps across two rows, then Election Day / Total sub-rows
    rows = [
        ['Title banner', 'Title banner'],                 # skipped by first_data_row
        ['Big Creek Township, Precinct', ''],
        ['1', ''],
        ['Election Day', '172'],
        ['Total', '222'],
    ]
    schema = _schema(first_data_row=1, columns=[signatures.ColumnRole(index=1, role='candidate')],
                     method_labels={'Election Day': 'election_day', 'Total': 'total'})
    blocks = votes.walk_page(rows, schema)
    assert len(blocks) == 1
    assert blocks[0]['label'] == 'Big Creek Township, Precinct 1'
    assert set(blocks[0]['methods']) == {'election_day', 'total'}


def test_walk_page_stops_at_terminal_skip_label():
    rows = [
        ['Precinct A', ''], ['Total', '10'],
        ['County Total', '99'],                            # skip label after real blocks -> stop
        ['Total', '99'],                                   # trailing junk, must not become a block
    ]
    schema = _schema(columns=[signatures.ColumnRole(index=1, role='candidate')],
                     method_labels={'Total': 'total'}, skip_labels=['County Total'])
    blocks = votes.walk_page(rows, schema)
    assert [b['label'] for b in blocks] == ['Precinct A']


def test_precinct_groups_split_on_repeated_candidate():
    a = _schema(columns=[signatures.ColumnRole(index=1, role='candidate', candidate='Harris')])
    b = _schema(columns=[signatures.ColumnRole(index=1, role='candidate', candidate='Oliver')])
    c = _schema(columns=[signatures.ColumnRole(index=1, role='candidate', candidate='Harris')])
    pages = [(a, []), (b, []), (c, [])]                    # Harris repeats on page 3 -> new group
    groups = votes._precinct_groups(pages)
    assert [len(g) for g in groups] == [2, 1]


def test_votes_to_rows_canonical_shape():
    votes_map = {('Precinct A', 'Kamala D. Harris', 'DEM'): {'votes': 222, 'election_day': 172}}
    rows = votes.votes_to_rows(votes_map, county='Oscoda', office='President')
    assert list(rows[0].keys()) == list(votes.CANON_COLUMNS)
    assert rows[0]['candidate'] == 'Kamala D. Harris' and rows[0]['votes'] == 222
    assert rows[0]['early_voting'] == ''


def test_split_row_joins_wrapped_label_and_collects_numbers():
    # label wrapped mid-word across two cells; spacer columns between values (precinct-major grid)
    label, numbers = votes._split_row(['LIB OLIVER and T', 'ER MAAT', '', '5', '', '2', '3', '0'])
    assert votes._norm(label) == votes._norm('LIB OLIVER and TER MAAT')
    assert numbers == [5, 2, 3, 0]


def test_norm_is_whitespace_and_case_insensitive():
    assert votes._norm('DEM HARRIS and  WALZ') == votes._norm('dem harris andwalz')


def test_contiguous_label_stops_at_gap():
    # precinct name may wrap into the adjacent cell; a far-column banner past a gap is dropped
    assert votes._contiguous_label(['Gettysburg', '1', '', '', 'banner'], 0) == 'Gettysburg 1'
    assert votes._contiguous_label(['110', '', '817 of', '1,056'], 0) == '110'


def test_assign_methods_equal_count_zips_in_order():
    rec = votes._assign_methods(['total', 'election_day', 'absentee_mail', 'provisional'],
                                [150, 100, 48, 2])
    assert rec == {'votes': 150, 'election_day': 100, 'absentee_mail': 48, 'provisional': 2}


def test_assign_methods_recovers_dropped_component_via_total_checksum():
    # a zero provisional cell was dropped -> 3 cells, 4 buckets; total (275=35+240) realigns them
    rec = votes._assign_methods(['election_day', 'absentee_mail', 'provisional', 'total'],
                                [35, 240, 275])
    assert rec == {'election_day': 35, 'absentee_mail': 240, 'provisional': 0, 'votes': 275}


def test_cell_count_reads_merged_count_and_skips_percent():
    assert votes._cell_count('1 100.00%') == 1        # count merged with its percent
    assert votes._cell_count('86.32%') is None        # pure percent -> not a count
    assert votes._cell_count('1,234') == 1234         # comma stripped


def test_assign_methods_ignores_a_spurious_wedged_cell_via_prefix_sum():
    # provisional 3 present, total 235 == 53+179+3; a stray 5 wedged before the total is dropped
    rec = votes._assign_methods(['election_day', 'absentee_mail', 'provisional', 'total'],
                                [53, 179, 3, 5, 235])
    assert rec == {'election_day': 53, 'absentee_mail': 179, 'provisional': 3, 'votes': 235}
