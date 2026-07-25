'''token_hits: which target tokens appear in a unit's text (substring or fuzzy).'''
from oe2d import contests


def test_substring_match():
    hits = contests.token_hits(['Harris', 'Trump'], 'Kamala D. Harris / Tim Walz (DEM)')
    assert 'Harris' in hits
    assert 'Trump' not in hits


def test_case_insensitive():
    assert 'HARRIS' in contests.token_hits(['HARRIS'], 'kamala harris walz')


def test_no_match_returns_empty():
    assert contests.token_hits(['Zzyzx'], 'kamala harris walz') == []


def test_party_token_substring():
    assert 'DEM' in contests.token_hits(['DEM'], 'harris walz (dem) 409')


def test_fuzzy_recovers_ocr_typo():
    # OCR reads 'Slotkin' as 'Slotkim'; the fuzzy word match should still catch it.
    hits = contests.token_hits(['Slotkin'], 'elissa slotkim democratic')
    assert 'Slotkin' in hits
