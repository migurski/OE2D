'''Tests for _match_header_line (hermetic: fragments + raw lines in, a clean label out).

read_text_grid reconstructs a precinct-major page by aligning text to the data columns below, so the
precinct-name header -- which spans the full page width -- gets cut wherever a numeric column's
x-boundary falls, sometimes MID-WORD ("Bangor Township Precinct 1" -> "Bangor Tow nship Precin ct 1").
The raw text layer still holds the line intact. _match_header_line restores the original spacing by
matching the fragmented join to the raw line with the same de-spaced content, so the gold precinct
name is source-faithful rather than carrying the grid's split artifacts.
'''
from ... import votes

LINES = ['Summary Results Report OFFICIAL RESULTS', '2024 General Election',
         'November 5, 2024 Bay County', 'Bangor Township Precinct 1',
         'Statistics TOTAL Election Absentee Early']


def test_restores_a_mid_word_split_from_the_raw_line():
    assert votes._match_header_line('Bangor Tow nship Precin ct 1', LINES) == 'Bangor Township Precinct 1'


def test_keeps_an_already_clean_label():
    assert votes._match_header_line('Bangor Township Precinct 1', LINES) == 'Bangor Township Precinct 1'


def test_ignores_spacing_and_punctuation_when_matching():
    lines = ['Bergland Township, Precinct 1']
    assert votes._match_header_line('Bergland Tow nship Precinct1', lines) == 'Bergland Township, Precinct 1'


def test_falls_back_to_fragments_when_no_line_matches():
    # a fragment set naming no raw line (wrong page) is returned unchanged rather than mis-snapped
    assert votes._match_header_line('Some Other Precinct 9', LINES) == 'Some Other Precinct 9'


def test_empty_fragments_return_unchanged():
    assert votes._match_header_line('', LINES) == ''
