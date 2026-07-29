'''Tests for the shared per-field scorer (used by evaluate + optimize; hermetic, no LM).'''
import dspy

from ...pages import evaluate, metrics


class _StubProgram:
    '''Stand-in for PageAnalyzer: returns fixed field values, no LM call.'''
    def __init__(self, **fields):
        self._fields = fields

    def __call__(self, image):
        return dspy.Prediction(**self._fields)


_GOLD = dict(candidate_orientation='columns', contest_name_present=True,
             candidate_names_present=True, headers_present=True,
             precinct_scope='multi_precinct', precinct_orientation='rows')


def _example():
    ex = dspy.Example(image='img', **_GOLD)
    ex._fixture = 'doc-a.pdf'
    return ex


def test_score_fields_all_correct():
    correct, total, misses = evaluate.score_fields(_StubProgram(**_GOLD), [_example()])
    assert not misses
    assert all(correct[f] == total[f] == 1 for f in metrics.FIELD_WEIGHTS)


def test_score_fields_reports_the_one_miss():
    wrong = dict(_GOLD, precinct_scope='county')
    correct, total, misses = evaluate.score_fields(_StubProgram(**wrong), [_example()])
    assert misses == [('doc-a.pdf', 'precinct_scope', 'county', 'multi_precinct')]
    assert correct['precinct_scope'] == 0 and total['precinct_scope'] == 1
    assert correct['candidate_orientation'] == 1        # the rest still score
