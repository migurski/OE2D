'''Contest surfacing and location: title-line capture, candidate surfacing, digit-collapsed
classification, occurrence-based location, and the interpret tools.

pagetext.layout_texts is mocked with canned page text, so these are hermetic -- no files, no
pdfplumber, no OCR.
'''
import pytest

from ... import contests


def _canned(pages):
    return lambda path, limit: [pages.get(u, '') for u in range(1, limit + 1)]


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


def test_title_lines_drops_page_banner_above_marker():
    text = 'Page: 19 of 247 11/13/2024\nRepresentative in Congress 1st District (Vote for 1)\n1 2'
    assert contests._title_lines(text) == ['Representative in Congress 1st District (Vote for 1)']


def test_table_titles_reads_heading_above_a_vote_table():
    # a proposition -- no "vote for" marker, short title -- is the line above its yes/no table
    text = 'Run Date 12/03/2024 Page 3\nPROPOSITION 3\nYES 254 55.22\nNO 206 44.78'
    assert contests._table_titles(text) == ['PROPOSITION 3']


def test_page_titles_unions_marker_table_and_heading():
    text = ('PRESIDENT AND VICE PRESIDENT (Vote for 1)\nHarris 10 20\n'
            'PROPOSITION 3\nYES 254 55\nNO 206 44')
    got = contests._page_titles(text)
    assert any('PRESIDENT' in t for t in got)          # via the "vote for" marker
    assert 'PROPOSITION 3' in got                       # via the table-anchor (no marker)


def test_heading_candidates_is_structural_only_no_lexicon():
    # No word-list: any multi-word non-data line is a candidate -- a real contest, a candidate-name
    # fragment, and a subtotal label all pass. Culling them is the LLM classifier's job.
    assert contests._heading_candidates('1 U.S. Representative, 12th Congressional') \
        == ['1 U.S. Representative, 12th Congressional']          # real race (2 numbers ok)
    assert contests._heading_candidates('F. KENNEDY AI - ROBERT') == ['F. KENNEDY AI - ROBERT']  # junk kept
    assert contests._heading_candidates('3rd Assembly District') == ['3rd Assembly District']    # subtotal kept
    assert contests._heading_candidates('200100 Election Day 3535 256 7') == []   # data row rejected
    assert contests._heading_candidates('MIMI') == []                             # too short


def test_locate_pages_occurrence_vs_span():
    title_pages = [2, 7, 12, 20]
    # a recurring title (>= _RECUR_MIN occurrences) -> the occurrences ARE the pages, no span
    assert contests._locate_pages([2, 7, 12], title_pages, 25) == {2, 7, 12}
    # a title seen once -> it heads a block, span to the next title page
    assert contests._locate_pages([2], title_pages, 25) == {2, 3, 4, 5, 6}
    # last title, one occurrence -> runs to unit_count
    assert contests._locate_pages([20], title_pages, 25) == set(range(20, 26))


def test_classify_headers_trusts_llm_and_is_fatal_when_unavailable():
    import types
    loc = contests.ContestLocator()
    cands = ['1 President and Vice President', 'F. KENNEDY AI - ROBERT', '3rd Assembly District']
    # LLM keeps only the real contest, verbatim (case-tolerant) -> junk + subtotal culled.
    loc.classify = lambda candidates: types.SimpleNamespace(
        contest_titles=['1 president and vice president'])
    assert loc._classify_headers(cands) == ['1 President and Vice President']
    assert loc._classify_headers([]) == []
    # An unavailable LM is fatal -- the failure propagates, it is not swallowed.
    def boom(**kwargs):
        raise RuntimeError('no LM')
    loc.classify = boom
    with pytest.raises(RuntimeError):
        loc._classify_headers(cands)


def test_classify_headers_collapses_digit_only_variants():
    import types
    loc = contests.ContestLocator()
    seen: list[str] = []

    def fake_classify(candidates):
        seen.extend(candidates)               # record what the LLM actually receives
        return types.SimpleNamespace(
            contest_titles=[c for c in candidates if 'Representative' in c])

    loc.classify = fake_classify
    cands = [
        '1 U.S. Representative, 12th District',    # collapses with the 14th -> "...11th..."
        '1 U.S. Representative, 14th District',
        'Precinct 001 registration total',        # collapses with 002 -> "Precinct 111..."
        'Precinct 002 registration total',
    ]
    chosen = loc._classify_headers(cands)
    # only two digit-collapsed forms reach the LLM, not four
    assert len(seen) == 2
    # and the model saw realistic collapsed values (digits replaced, not blanked)
    assert '1 U.S. Representative, 11th District' in seen
    assert 'Precinct 111 registration total' in seen
    # the kept form expands back to BOTH original district variants (kept verbatim)
    assert chosen == ['1 U.S. Representative, 12th District',
                      '1 U.S. Representative, 14th District']


def test_contest_evidence_distinct_titles_with_sample(monkeypatch):
    pages = {2: 'President/Vice-President of the United States (Vote for 1)\nHarris Trump Walz',
             3: 'United States Senator (Vote for 1)\nSlotkin Rogers'}
    monkeypatch.setattr(contests.pagetext, 'layout_texts', _canned(pages))
    evidence, units = contests.contest_evidence('x.pdf', unit_count=3)  # target-agnostic vocabulary
    pres = next(e for e in evidence if 'President/Vice-President' in e.title)
    assert pres.units == [2]
    assert 'Harris' in pres.sample and 'Trump' in pres.sample     # candidate rows under the title


def test_locator_uses_llm_chosen_titles(monkeypatch):
    import types
    pages = {2: 'Representative in Congress 1st District (Vote for 1)\nBergman Barr',
             4: 'United States Senator (Vote for 1)\nSlotkin'}
    monkeypatch.setattr(contests.pagetext, 'layout_texts', _canned(pages))
    loc = contests.ContestLocator()
    # classify keeps the surfaced strings (the LLM cull is exercised in its own test).
    loc.classify = lambda candidates: types.SimpleNamespace(contest_titles=list(candidates))
    # LLM maps "U.S. House" to the Congress title a literal string match could not.
    loc.match = lambda contest, context: types.SimpleNamespace(
        matching_titles=['Representative in Congress 1st District (Vote for 1)'])
    target = contests.Target(contest='U.S. House', context='House race, Bergman vs Barr')
    pred = loc(file_path='x.pdf', targets=[target], unit_count=6)
    assert pred.locations[0].pages == [2, 3]             # Congress title -> next title - 1, as a page set


def test_locator_is_fatal_when_llm_unavailable(monkeypatch):
    import types
    pages = {2: 'President/Vice-President of the United States (Vote for 1)', 4: 'Senator (Vote for 1)'}
    monkeypatch.setattr(contests.pagetext, 'layout_texts', _canned(pages))
    loc = contests.ContestLocator()
    loc.classify = lambda candidates: types.SimpleNamespace(contest_titles=list(candidates))
    def boom(**kwargs):
        raise RuntimeError('no LM configured')
    loc.match = boom
    # An unavailable LM is fatal: the failure propagates instead of degrading to a heuristic.
    with pytest.raises(RuntimeError):
        loc(file_path='x.pdf', targets=[contests.Target(contest='President')], unit_count=6)


def test_locator_title_search_tools():
    loc = contests.ContestLocator()
    loc._evidence = [
        contests.TitleEvidence(title='PRESIDENT AND VICE PRESIDENT - Vote for One',
                               units=[1, 8], sample='Kamala D. Harris ... Donald J. Trump ...'),
        contests.TitleEvidence(title='U.S. SENATOR, FULL TERM - Vote for One', units=[1],
                               sample='Adam B. Schiff ... Steve Garvey ...'),
        contests.TitleEvidence(title='U.S. SENATOR, PARTIAL/UNEXPIRED TERM - Vote for One',
                               units=[1], sample='Adam B. Schiff ... Steve Garvey ...'),
    ]
    assert loc.search_titles('senator') == ['U.S. SENATOR, FULL TERM - Vote for One',
                                            'U.S. SENATOR, PARTIAL/UNEXPIRED TERM - Vote for One']
    assert 'Harris' in loc.inspect_title('PRESIDENT AND VICE PRESIDENT - Vote for One')
    # A non-title and a PARTIAL of a real title both raise (ReAct feeds the error back as an
    # observation), so the agent can't confirm a truncated title -- e.g. one with a leading
    # contest number dropped -- and carry it forward.
    with pytest.raises(ValueError):
        loc.inspect_title('no such contest')
    with pytest.raises(ValueError):
        loc.inspect_title('PRESIDENT AND VICE PRESIDENT')       # partial of a real title
    assert len(loc.list_titles()) == 3
