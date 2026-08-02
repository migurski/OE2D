'''Tests for the report-line reader's pure core (_word_lines): clustering pdfplumber words into visual
lines by their top-y. The Dominion per-precinct reports (Nevada "Precinct Results Report", Mono
"Election Summary Report") wrap a candidate name across lines with a party/value line floating between
the fragments, so read_report_blocks reconstructs the grid from word geometry rather than the
over-fragmented text-strategy grid. _word_lines is the geometry step every such read starts from;
pinning it keeps the clustering (y-tolerance grouping, left-to-right ordering) stable.
'''
from ... import votes


def _word(text, x0, top):
    return {'text': text, 'x0': x0, 'x1': x0 + 10 * len(text), 'top': top}


def test_groups_words_within_the_y_tolerance_into_one_line():
    words = [_word('BIDEN', 20, 100.0), _word('HARRIS', 90, 101.5), _word('210', 250, 100.5)]
    lines = votes._word_lines(words)
    assert len(lines) == 1
    assert [w['text'] for w in lines[0]] == ['BIDEN', 'HARRIS', '210']    # sorted left-to-right by x0


def test_separates_lines_beyond_the_tolerance():
    words = [_word('BIDEN', 20, 100.0), _word('HARRIS', 20, 110.0)]      # 10 apart > default 3
    lines = votes._word_lines(words)
    assert [[w['text'] for w in line] for line in lines] == [['BIDEN'], ['HARRIS']]


def test_orders_lines_top_to_bottom_regardless_of_input_order():
    words = [_word('third', 20, 130.0), _word('first', 20, 100.0), _word('second', 20, 115.0)]
    lines = votes._word_lines(words)
    assert [line[0]['text'] for line in lines] == ['first', 'second', 'third']


def test_empty_input_yields_no_lines():
    assert votes._word_lines([]) == []
