'''Tests for join_flat_table_pages (hermetic: no Textract, no LM, no source files).

join_flat_table_pages reads a candidate-GROUP flat contest -- one whose candidate columns are split
across pages that all repeat the same precincts (a Hart SOVC too wide for one page). It runs
scope_flat_tables per page and joins the pages by precinct: unions a precinct's disjoint candidate
columns and sums its write-in rows. Both impure dependencies are injected, so a stub schema resolver
(keyed on each page's header, since the pages carry different candidates) fully drives it, and tiny
hand-built grids stand in for what Textract returns per page. These mirror the scope_flat_tables
tests: one behaviour per case (the cross-page union, precinct-name matching, write-in summing, total
union).
'''
from ... import votes
from ...votes import signatures


def _col(index, candidate, party='', write_in=False):
    return signatures.ColumnRole(index=index, role='candidate', candidate=candidate, party=party,
                                 write_in=write_in)


def _schema(columns):
    return signatures.PageSchema(first_data_row=0, label_column=0, columns=list(columns),
                                 method_labels={}, skip_labels=[])


def _resolver(by_token):
    '''A stub schema_for: pick the schema whose token appears in the anchor grid's header row. Stands
    in for the interpreter, which returns a different schema per page (each page's own candidates).'''
    def schema_for(anchor):
        header = ' '.join(anchor[0]).upper()
        for token, schema in by_token.items():
            if token.upper() in header:
                return schema
        raise AssertionError('no stub schema for header %r' % header)
    return schema_for


def test_unions_disjoint_candidate_columns_across_pages():
    # two pages, the SAME precinct P1, DIFFERENT candidates; the join gives P1 all four candidates
    page_a = [['', 'Harris', 'Trump'], ['P1', '10', '20'], ['Total', '10', '20']]
    page_b = [['', 'Oliver', 'Scattered'], ['P1', '3', '1'], ['Total', '3', '1']]
    schema_for = _resolver({
        'Harris': _schema([_col(1, 'Harris', 'DEM'), _col(2, 'Trump', 'REP')]),
        'Oliver': _schema([_col(1, 'Oliver', 'LIB'), _col(2, 'Scattered', write_in=True)])})
    vote_map, _totals = votes.join_flat_table_pages(
        [[page_a], [page_b]], 'Harris\nTrump\nOliver', schema_for)
    assert vote_map[('P1', 'Harris', 'DEM')] == {'votes': 10}
    assert vote_map[('P1', 'Oliver', 'LIB')] == {'votes': 3}
    assert vote_map[('P1', votes.WRITE_IN_LABEL, '')] == {'votes': 1}
    assert {precinct for (precinct, _c, _p) in vote_map} == {'P1'}   # one precinct, not two


def test_matches_precincts_across_pages_by_normalized_name_keeping_the_first_label():
    # the scan renders the same precinct differently per page ("01 - Alpha" vs "01 Alpha"); the join
    # matches them and keeps the FIRST page's label
    page_a = [['', 'Harris'], ['01 - Alpha', '10'], ['Total', '10']]
    page_b = [['', 'Oliver'], ['01 Alpha', '3'], ['Total', '3']]
    schema_for = _resolver({'Harris': _schema([_col(1, 'Harris', 'DEM')]),
                            'Oliver': _schema([_col(1, 'Oliver', 'LIB')])})
    vote_map, _totals = votes.join_flat_table_pages(
        [[page_a], [page_b]], 'Harris\nOliver', schema_for)
    precincts = {precinct for (precinct, _c, _p) in vote_map}
    assert precincts == {'01 - Alpha'}                              # matched to one, first label kept
    assert vote_map[('01 - Alpha', 'Oliver', 'LIB')] == {'votes': 3}


def test_sums_write_in_rows_across_pages():
    # each page consolidates its own write-in columns into one Write-ins row; across pages they ADD
    page_a = [['', 'Harris', 'Carroll'], ['P1', '10', '3'], ['Total', '10', '3']]
    page_b = [['', 'Pierce', 'Ventura'], ['P1', '1', '0'], ['Total', '1', '0']]
    schema_for = _resolver({
        'Harris': _schema([_col(1, 'Harris', 'DEM'), _col(2, 'Carroll', write_in=True)]),
        'Pierce': _schema([_col(1, 'Pierce', write_in=True), _col(2, 'Ventura', write_in=True)])})
    vote_map, _totals = votes.join_flat_table_pages(
        [[page_a], [page_b]], 'Harris', schema_for)
    assert vote_map[('P1', votes.WRITE_IN_LABEL, '')] == {'votes': 4}   # 3 (page a) + 1 (page b)


def test_unions_printed_totals_across_pages():
    # each page has its own candidates' county totals; the join collects them all (the reconcile
    # checksum needs every candidate's printed total, and they are spread across the pages)
    page_a = [['', 'Harris', 'Trump'], ['P1', '10', '20'], ['Total', '10', '20']]
    page_b = [['', 'Oliver'], ['P1', '3'], ['Total', '3']]
    schema_for = _resolver({
        'Harris': _schema([_col(1, 'Harris', 'DEM'), _col(2, 'Trump', 'REP')]),
        'Oliver': _schema([_col(1, 'Oliver', 'LIB')])})
    vote_map, totals = votes.join_flat_table_pages(
        [[page_a], [page_b]], 'Harris\nTrump\nOliver', schema_for)
    assert totals == {('Harris', 'DEM'): 10, ('Trump', 'REP'): 20, ('Oliver', 'LIB'): 3}
    assert votes._reconciles(vote_map, totals)                     # single precinct sums to each total
