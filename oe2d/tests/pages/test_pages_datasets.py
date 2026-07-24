'''Tests for the page-analysis dataset loader (hermetic).

The split-invariant tests build dspy.Example objects directly (no images needed);
the normalization test points at one committed page image.
'''
import os

import dspy

from oe2d.pages import datasets

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_IMAGES = os.path.join(_REPO_ROOT, 'oe2d-data', 'pages', 'images')


def _example(fixture: str, synthetic: bool) -> dspy.Example:
    example = dspy.Example(marker=1).with_inputs('marker')
    example._fixture = fixture
    example._synthetic = synthetic
    return example


def test_split_keeps_synthetic_out_of_val():
    examples = [
        _example('../fixtures/categorize/a.pdf', False),
        _example('../fixtures/categorize/b.pdf', False),
        _example('../fixtures/categorize/c.pdf', False),
        _example('../fixtures/categorize/d.pdf', False),
        _example('../fixtures/categorize/b.pdf', True),   # synthetic derived from a train fixture
    ]
    trainset, valset = datasets.split(examples, val_fraction=0.5)
    assert all(not ex._synthetic for ex in valset), 'no synthetic row may land in val'
    assert sum(ex._synthetic for ex in trainset) >= 1


def test_split_drops_synthetics_derived_from_val_fixtures():
    # a rotated/cropped page of a VAL fixture must not sneak into train, or the
    # (transformed) content leaks against its own val pages.
    examples = [_example(f'../fixtures/categorize/{c}.pdf', False) for c in 'abcd']
    trainset, valset = datasets.split(examples, val_fraction=0.5)
    val_fixtures = {ex._fixture for ex in valset}
    a_val_fixture = next(iter(val_fixtures))
    examples.append(_example(a_val_fixture, True))    # synthetic of a val fixture
    trainset, valset = datasets.split(examples, val_fraction=0.5)
    assert all(ex._fixture != a_val_fixture for ex in trainset if ex._synthetic), \
        'synthetics of a val fixture must be dropped, not trained on'


def test_split_no_fixture_leak():
    examples = [_example(f'../fixtures/categorize/{c}.pdf', False) for c in 'abcdef']
    # two pages from the same fixture must not straddle the split
    examples.append(_example('../fixtures/categorize/a.pdf', False))
    trainset, valset = datasets.split(examples, val_fraction=0.5)
    train_fixtures = {ex._fixture for ex in trainset}
    val_fixtures = {ex._fixture for ex in valset}
    assert train_fixtures.isdisjoint(val_fixtures), 'a fixture must be wholly in one side'


def test_split_is_deterministic():
    examples = [_example(f'../fixtures/categorize/{c}.pdf', False) for c in 'abcdefgh']
    first = datasets.split(examples, val_fraction=0.25)
    second = datasets.split(examples, val_fraction=0.25)
    assert [e._fixture for e in first[1]] == [e._fixture for e in second[1]]


def test_record_to_example_normalizes_precinct_orientation():
    # a candidate-rows page has null precinct_orientation in the labels; the
    # example must carry the signature's concrete 'none' instead.
    any_image = sorted(os.listdir(_IMAGES))[0]
    record = {
        'image': f'images/{any_image}',
        'candidate_orientation': 'rows', 'contest_name_present': True,
        'candidate_names_present': True, 'headers_present': True,
        'precinct_scope': 'county', 'precinct_orientation': None,
        'skew_degrees': 0.0,
    }
    example = datasets.record_to_example(record)
    assert example.precinct_orientation == 'none'
    assert example.candidate_orientation == 'rows'
    assert 'image' in example.inputs()
