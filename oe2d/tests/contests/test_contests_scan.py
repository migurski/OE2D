'''scan_for_targets: per-unit matching, early-stop, and the page budget.

The per-unit text reader (pagetext.unit_text) is mocked with canned page text, so
these are hermetic -- no files, no OCR, no tesseract.
'''
from oe2d import contests


def _canned_reader(pages, calls=None):
    '''Return a unit_text stand-in serving pages[unit], recording units read.'''
    def read(path, unit):
        if calls is not None:
            calls.append(unit)
        return pages.get(unit, '')
    return read


def test_records_hits_and_matched_tokens(monkeypatch):
    pages = {1: 'cover sheet', 2: 'President Harris Trump Walz',
             3: 'more precincts Harris Walz', 4: 'unrelated'}
    monkeypatch.setattr(contests.pagetext, 'unit_text', _canned_reader(pages))
    targets = [contests.Target(contest='President', hints=['Harris', 'Trump', 'Walz'])]

    hits = contests.scan_for_targets('x.pdf', targets, unit_count=4)

    assert [h.unit for h in hits] == [2, 3]
    assert set(hits[0].matched['President']) >= {'Harris', 'Trump', 'Walz'}


def test_early_stop_once_target_run_closes(monkeypatch):
    calls: list[int] = []
    pages = {1: 'President Harris Trump'}
    pages.update({u: 'nothing' for u in range(2, 9)})
    monkeypatch.setattr(contests.pagetext, 'unit_text', _canned_reader(pages, calls))
    targets = [contests.Target(contest='President', hints=['Harris', 'Trump'])]

    contests.scan_for_targets('x.pdf', targets, unit_count=8, max_gap=2)

    # Hit on unit 1; the run closes when unit-1 > max_gap (unit 4), so it stops there.
    assert max(calls) == 4
    assert 8 not in calls


def test_unseen_target_scans_to_end(monkeypatch):
    calls: list[int] = []
    pages = {u: 'no candidates here' for u in range(1, 6)}
    monkeypatch.setattr(contests.pagetext, 'unit_text', _canned_reader(pages, calls))
    targets = [contests.Target(contest='Senate', hints=['Slotkin'])]

    contests.scan_for_targets('x.pdf', targets, unit_count=5)

    assert max(calls) == 5      # never found -> cannot early-stop, scans everything


def test_requires_two_distinctive_matches(monkeypatch):
    # A lone common surname from another race ("Stein") must not qualify President.
    pages = {1: 'Dave Stein (UST) some other race', 2: 'President Harris Walz Trump'}
    monkeypatch.setattr(contests.pagetext, 'unit_text', _canned_reader(pages))
    targets = [contests.Target(contest='President',
                               hints=['Harris', 'Walz', 'Trump', 'Vance', 'Oliver', 'Stein'])]

    hits = contests.scan_for_targets('x.pdf', targets, unit_count=2)

    assert [h.unit for h in hits] == [2]


def test_two_party_tokens_do_not_qualify(monkeypatch):
    # DEM + REP appear on every results page, so they are not evidence on their own.
    pages = {1: 'turnout (DEM) (REP) 409 512'}
    monkeypatch.setattr(contests.pagetext, 'unit_text', _canned_reader(pages))
    targets = [contests.Target(contest='President', hints=['Harris', 'Trump', 'DEM', 'REP'])]

    assert contests.scan_for_targets('x.pdf', targets, unit_count=1) == []


def test_sparse_target_accepts_single_match(monkeypatch):
    # A target with fewer than two distinctive hints stays loose (one match counts).
    pages = {1: 'Representative in Congress Bergman'}
    monkeypatch.setattr(contests.pagetext, 'unit_text', _canned_reader(pages))
    targets = [contests.Target(contest='U.S. House', hints=['Bergman'])]

    hits = contests.scan_for_targets('x.pdf', targets, unit_count=1)

    assert [h.unit for h in hits] == [1]


def test_page_budget_caps_the_scan(monkeypatch):
    calls: list[int] = []
    pages = {u: 'nothing' for u in range(1, 101)}
    monkeypatch.setattr(contests.pagetext, 'unit_text', _canned_reader(pages, calls))
    targets = [contests.Target(contest='X', hints=['zzz'])]

    contests.scan_for_targets('x.pdf', targets, unit_count=100, page_budget=10)

    assert max(calls) == 10
