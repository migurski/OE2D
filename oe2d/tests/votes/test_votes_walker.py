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
