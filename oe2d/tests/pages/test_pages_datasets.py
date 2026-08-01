'''Tests for the page-analysis dataset loader (hermetic).

Routing tests build dspy.Example objects directly (no images needed); the
normalization test points at one committed page image.
'''
import os

import dspy

from ...pages import datasets

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_IMAGES = os.path.join(_REPO_ROOT, 'oe2d-data', 'pages', 'images')


def _example(fixture: str, synthetic: bool = False) -> dspy.Example:
    example = dspy.Example(marker=1).with_inputs('marker')
    example._fixture = fixture
    example._synthetic = synthetic
    return example


def _reals(letters: str) -> list:
    return [_example(f'sample-{c}.pdf') for c in letters]


def _has(seq: list, obj) -> bool:
    # dspy.Example compares by value, so `in` is unreliable for identical stubs;
    # test membership by object identity instead.
    return any(item is obj for item in seq)


def _a_val_fixture(reals: list) -> str:
    return datasets.split(reals, val_fraction=0.5)[1][0]._fixture


def test_split_no_fixture_leak():
    reals = _reals('abcd') + [_example('sample-a.pdf')]  # two pages of a
    trainset, valset = datasets.split(reals, val_fraction=0.5)
    assert {e._fixture for e in trainset}.isdisjoint({e._fixture for e in valset})


def test_split_is_deterministic():
    reals = _reals('abcdefgh')
    first = datasets.split(reals, val_fraction=0.25)
    second = datasets.split(reals, val_fraction=0.25)
    assert [e._fixture for e in first[1]] == [e._fixture for e in second[1]]


def test_synthetic_of_train_fixture_trains():
    reals = _reals('abcd')
    val_fixtures = {e._fixture for e in datasets.split(reals, val_fraction=0.5)[1]}
    train_fixture = next(f'sample-{c}.pdf' for c in 'abcd'
                         if f'sample-{c}.pdf' not in val_fixtures)
    syn = _example(train_fixture, synthetic=True)
    trainset, valset = datasets.split(reals + [syn], val_fraction=0.5)
    assert _has(trainset, syn) and not _has(valset, syn)


def test_synthetic_of_val_fixture_dropped():
    # a header-crop of a val fixture is a synthetic content variant of a val page
    reals = _reals('abcd')
    syn = _example(_a_val_fixture(reals), synthetic=True)
    trainset, valset = datasets.split(reals + [syn], val_fraction=0.5)
    assert not _has(trainset, syn) and not _has(valset, syn)


def test_val_is_real_only():
    reals = _reals('abcd')
    syn = _example(_a_val_fixture(reals), synthetic=True)
    _, valset = datasets.split(reals + [syn], val_fraction=0.5)
    assert all(not getattr(e, '_synthetic', False) for e in valset)


def test_subsample_spreads_across_fixtures_deterministically():
    examples = []
    for c in 'abcd':
        for _ in range(3):
            examples.append(_example(f'sample-{c}.pdf'))
    picked = datasets.subsample(examples, 4)
    assert len(picked) == 4
    assert len({ex._fixture for ex in picked}) == 4
    assert [e._fixture for e in datasets.subsample(examples, 6)] == \
           [e._fixture for e in datasets.subsample(examples, 6)]
    assert datasets.subsample(examples, 999) is examples


def test_record_to_example_normalizes_precinct_orientation():
    # first actual PNG, skipping OS junk (a stray .DS_Store must not be picked as the "image")
    any_image = next(name for name in sorted(os.listdir(_IMAGES)) if name.lower().endswith('.png'))
    record = {
        'image': f'images/{any_image}',
        'candidate_orientation': 'rows', 'contest_name_present': True,
        'candidate_names_present': True, 'headers_present': True,
        'precinct_scope': 'county', 'precinct_orientation': None,
    }
    example = datasets.record_to_example(record)
    assert example.precinct_orientation == 'none'   # null -> concrete literal
    assert example.candidate_orientation == 'rows'
    assert 'image' in example.inputs()
