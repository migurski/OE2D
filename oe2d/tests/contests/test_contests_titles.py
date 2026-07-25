'''Title-based locating: word matching, title-line capture, index, and segmentation.

pagetext.layout_texts is mocked with canned page text, so these are hermetic -- no
files, no pdfplumber, no OCR.
'''
from oe2d import contests


def test_title_matches_is_conservative_exact_word_overlap():
    # Exact only: matches when every significant target word is present verbatim...
    pres = contests.Target(contest='President', hints=[])
    assert contests._title_matches(pres, 'President/Vice-President of the United States (Vote for 1)')
    board = contests.Target(contest='State Board of Education', hints=[])
    assert contests._title_matches(board, 'Member of the State Board of Education (Vote for 2)')
    # ...and deliberately does NOT do fuzzy wording -- that is the LLM/tools' job:
    assert not contests._title_matches(pres, 'PRESIDENTIAL ELECTORS Vote For 1')       # presidential != president
    senate = contests.Target(contest='U.S. Senate', hints=[])
    assert not contests._title_matches(senate, 'United States Senator (Vote for 1)')   # senate != senator
    house = contests.Target(contest='U.S. House', hints=[])
    assert not contests._title_matches(house, 'Representative in Congress (Vote for 1)')


def test_title_lines_joins_marker_with_line_above():
    text = 'PRESIDENTIAL ELECTORS\nVote For 1\nsome row 12 34'
    lines = contests._title_lines(text)
    assert lines == ['PRESIDENTIAL ELECTORS Vote For 1']


def test_title_lines_captures_every_marker_on_a_page():
    text = ('Straight Party Ticket Vote for 1\n409 512\n'
            'President/Vice-President of the United States Vote for 1\n1 2')
    lines = contests._title_lines(text)
    assert len(lines) == 2
    assert any('President/Vice-President' in l for l in lines)


def _canned(pages):
    return lambda path, limit: [pages.get(u, '') for u in range(1, limit + 1)]


def test_contest_title_index_records_titles_per_unit(monkeypatch):
    pages = {2: 'President/Vice-President of the United States (Vote for 1)',
             3: 'United States Senator (Vote for 1)', 4: 'precinct rows only'}
    monkeypatch.setattr(contests.pagetext, 'layout_texts', _canned(pages))
    index = contests.contest_title_index('x.pdf', unit_count=4)
    assert set(index) == {2, 3}                       # unit 4 has no title


def test_title_segments_by_contest_runs_to_next_title(monkeypatch):
    pages = {2: 'President/Vice-President of the United States (Vote for 1)',
             5: 'United States Senator (Vote for 1)'}
    monkeypatch.setattr(contests.pagetext, 'layout_texts', _canned(pages))
    index = contests.contest_title_index('x.pdf', unit_count=8)
    pres = contests.Target(contest='President', hints=[])
    # President title p2, next title p5 -> span [2, 4].
    assert contests.title_segments(index, pres, 8) == [(2, 4)]


def test_title_segments_by_precinct_one_span_per_recurrence(monkeypatch):
    block = 'Electors of President and Vice-President Vote for not more than 1'
    other = 'United States Senator Vote for not more than 1'
    pages = {1: block, 2: other, 8: block, 9: other}   # two precinct blocks
    monkeypatch.setattr(contests.pagetext, 'layout_texts', _canned(pages))
    index = contests.contest_title_index('x.pdf', unit_count=14)
    pres = contests.Target(contest='President', hints=[])
    # matches p1 and p8; each runs to the next title - 1.
    assert contests.title_segments(index, pres, 14) == [(1, 1), (8, 8)]


def test_title_segments_last_title_runs_to_unit_count(monkeypatch):
    pages = {5: 'President/Vice-President of the United States (Vote for 1)'}
    monkeypatch.setattr(contests.pagetext, 'layout_texts', _canned(pages))
    index = contests.contest_title_index('x.pdf', unit_count=7)
    pres = contests.Target(contest='President', hints=[])
    assert contests.title_segments(index, pres, 7) == [(5, 7)]


def test_title_lines_drops_page_banner_above_marker():
    text = 'Page: 19 of 247 11/13/2024\nRepresentative in Congress 1st District (Vote for 1)\n1 2'
    assert contests._title_lines(text) == ['Representative in Congress 1st District (Vote for 1)']


def test_segments_for_titles_selects_only_chosen_titles():
    index = {2: ['President/Vice-President of the United States (Vote for 1)'],
             5: ['United States Senator (Vote for 1)']}
    spans = contests.segments_for_titles(index, ['United States Senator (Vote for 1)'], 9)
    assert spans == [(5, 9)]                      # only the chosen title, to doc end


def test_contest_evidence_distinct_titles_with_header_tokens(monkeypatch):
    pages = {2: 'President/Vice-President of the United States (Vote for 1)\nHarris Trump Walz',
             3: 'United States Senator (Vote for 1)\nSlotkin Rogers'}
    monkeypatch.setattr(contests.pagetext, 'layout_texts', _canned(pages))
    target = contests.Target(contest='President', hints=['Harris', 'Trump', 'Walz'])
    index, evidence, units = contests.contest_evidence('x.pdf', target, unit_count=3)
    titles = {e.title for e in evidence}
    assert any('President/Vice-President' in t for t in titles)
    pres = next(e for e in evidence if 'President/Vice-President' in e.title)
    assert set(pres.header_tokens) >= {'Harris', 'Trump', 'Walz'} and pres.units == [2]


def test_locator_uses_llm_chosen_titles(monkeypatch):
    import types
    pages = {2: 'Representative in Congress 1st District (Vote for 1)\nBergman Barr',
             4: 'United States Senator (Vote for 1)\nSlotkin'}
    monkeypatch.setattr(contests.pagetext, 'layout_texts', _canned(pages))
    loc = contests.ContestLocator()
    # LLM maps "U.S. House" to the Congress title the deterministic prefix cannot match.
    loc.match = lambda contest, context: types.SimpleNamespace(
        matching_titles=['Representative in Congress 1st District (Vote for 1)'])
    target = contests.Target(contest='U.S. House', context='House race, Bergman vs Barr')
    pred = loc(file_path='x.pdf', targets=[target], unit_count=6)
    assert pred.locations[0].ranges == [(2, 3)]         # Congress title -> next title - 1


def test_locator_falls_back_to_deterministic_when_llm_fails(monkeypatch):
    pages = {2: 'President/Vice-President of the United States (Vote for 1)', 4: 'Senator (Vote for 1)'}
    monkeypatch.setattr(contests.pagetext, 'layout_texts', _canned(pages))
    loc = contests.ContestLocator()
    def boom(**kwargs):
        raise RuntimeError('no LM configured')
    loc.match = boom
    pred = loc(file_path='x.pdf', targets=[contests.Target(contest='President')], unit_count=6)
    assert pred.locations[0].ranges == [(2, 3)]         # deterministic _title_matches carried it


def test_title_matches_rejects_other_contests():
    # exact overlap naturally keeps "President" off SENATOR / REPRESENTATIVE titles
    pres = contests.Target(contest='President', hints=[])
    assert not contests._title_matches(pres, 'U.S. SENATOR, FULL TERM - Vote for One')
    assert not contests._title_matches(pres, 'U.S. REPRESENTATIVE DISTRICT 2 - Vote for One')
    assert contests._title_matches(pres, 'PRESIDENT AND VICE PRESIDENT - Vote for One')


def test_locator_title_search_tools():
    loc = contests.ContestLocator()
    loc._evidence = [
        contests.TitleEvidence(title='PRESIDENT AND VICE PRESIDENT - Vote for One',
                               units=[1, 8], header_tokens=['Harris', 'Trump']),
        contests.TitleEvidence(title='U.S. SENATOR, FULL TERM - Vote for One', units=[1],
                               header_tokens=['Schiff']),
        contests.TitleEvidence(title='U.S. SENATOR, PARTIAL/UNEXPIRED TERM - Vote for One',
                               units=[1], header_tokens=['Schiff']),
    ]
    assert loc.search_titles('senator') == ['U.S. SENATOR, FULL TERM - Vote for One',
                                            'U.S. SENATOR, PARTIAL/UNEXPIRED TERM - Vote for One']
    assert loc.titles_with_candidate('harris') == ['PRESIDENT AND VICE PRESIDENT - Vote for One']
    assert len(loc.list_titles()) == 3
