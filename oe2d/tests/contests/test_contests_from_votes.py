'''from_votes: translating votes gold records into contest-locating gold rows.'''
from ...contests import from_votes


def test_has_district_treats_none_empty_and_list_as_absent():
    assert from_votes.has_district('5') is True
    assert from_votes.has_district(None) is False
    assert from_votes.has_district('') is False
    assert from_votes.has_district([]) is False


def test_target_label_districts_only_when_present():
    assert from_votes.target_label('U.S. House', '5') == 'U.S. House District 5'
    assert from_votes.target_label('President', None) == 'President'
    assert from_votes.target_label('Straight Party', '') == 'Straight Party'


def test_to_contest_row_maps_pages_candidates_and_provenance():
    row = from_votes.to_contest_row({
        'id': 'x-president', 'source_url': 'http://e/x.pdf', 'office': 'President',
        'district': None, 'observed_title': 'President/Vice-President',
        'candidate_context': ['Harris', 'Trump'], 'pages': [12, 10, 11]})
    assert row['target'] == 'President'
    assert row['pages'] == [10, 11, 12]           # sorted
    assert row['range'] == [10, 12]               # min/max span
    assert row['candidates'] == ['Harris', 'Trump']
    assert row['observed_title'] == 'President/Vice-President'
    assert row['provenance'] == 'votes'


def test_convert_skips_existing_pair_but_keeps_new_target_on_shared_doc():
    votes = [
        {'source_url': 'http://e/a.pdf', 'office': 'President', 'district': None, 'pages': [1, 2]},
        {'source_url': 'http://e/a.pdf', 'office': 'U.S. House', 'district': '17', 'pages': [5]},
    ]
    existing = {('http://e/a.pdf', 'President')}       # curated already has this doc's President
    out = from_votes.convert(votes, existing)
    assert [r['target'] for r in out] == ['U.S. House District 17']


def test_convert_drops_rows_without_pages_and_dedups_within_votes():
    votes = [
        {'source_url': 'http://e/b.pdf', 'office': 'President', 'district': None, 'pages': []},
        {'source_url': 'http://e/b.pdf', 'office': 'U.S. Senate', 'district': None, 'pages': [3]},
        {'source_url': 'http://e/b.pdf', 'office': 'U.S. Senate', 'district': None, 'pages': [3]},
    ]
    out = from_votes.convert(votes, set())
    assert [r['target'] for r in out] == ['U.S. Senate']       # empty-pages dropped, dup collapsed
