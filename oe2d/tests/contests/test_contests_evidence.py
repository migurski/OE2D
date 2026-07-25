'''build_evidence: the trailing pad that extends a run past its last matched unit.'''
from oe2d import contests


def _hit(unit, tokens):
    return contests.UnitHit(unit=unit, matched={'President': tokens}, title=f'p{unit}')


_TARGET = [contests.Target(contest='President', hints=['Harris', 'Trump', 'Walz'])]


def test_trailing_pad_extends_end():
    hits = [_hit(5, ['Harris', 'Trump']), _hit(6, ['Harris', 'Walz'])]
    evidence = contests.build_evidence(hits, _TARGET, max_gap=2, unit_count=20)
    # end 6 padded by max_gap 2 -> 8, to catch a nameless write-in/continuation tail.
    assert (evidence[0].unit_start, evidence[0].unit_end) == (5, 8)


def test_trailing_pad_clamped_to_unit_count():
    hits = [_hit(5, ['Harris', 'Trump'])]
    evidence = contests.build_evidence(hits, _TARGET, max_gap=2, unit_count=6)
    assert evidence[0].unit_end == 6      # 5 + 2 = 7, clamped to the last unit


def test_custom_trailing_pad():
    hits = [_hit(5, ['Harris', 'Trump'])]
    evidence = contests.build_evidence(hits, _TARGET, max_gap=2, trailing_pad=0, unit_count=20)
    assert evidence[0].unit_end == 5      # no pad
