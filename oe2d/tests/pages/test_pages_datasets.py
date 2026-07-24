'''Tests for the page-analysis dataset loader (hermetic).

The routing tests build dspy.Example objects directly (no images needed); the
normalization/eval_kind tests point at committed page images.
'''
import os

import dspy

from oe2d.pages import datasets

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_IMAGES = os.path.join(_REPO_ROOT, 'oe2d-data', 'pages', 'images')


def _example(fixture: str, synthetic: bool = False, transform=None) -> dspy.Example:
    example = dspy.Example(marker=1).with_inputs('marker')
    example._fixture = fixture
    example._synthetic = synthetic
    example._transform = transform
    return example


def _reals(letters: str) -> list:
    return [_example(f'../fixtures/categorize/{c}.pdf') for c in letters]


def _a_val_fixture(reals: list) -> str:
    return datasets.split(reals, val_fraction=0.5)[1][0]._fixture


def _has(seq: list, obj) -> bool:
    # dspy.Example compares by value, so `in` is unreliable for identical stubs;
    # test membership by object identity instead.
    return any(item is obj for item in seq)


def test_split_no_fixture_leak():
    reals = _reals('abcd') + [_example('../fixtures/categorize/a.pdf')]  # two pages of a
    trainset, valset = datasets.split(reals, val_fraction=0.5)
    assert {e._fixture for e in trainset}.isdisjoint({e._fixture for e in valset})


def test_split_is_deterministic():
    reals = _reals('abcdefgh')
    first = datasets.split(reals, val_fraction=0.25)
    second = datasets.split(reals, val_fraction=0.25)
    assert [e._fixture for e in first[1]] == [e._fixture for e in second[1]]


def test_rotation_of_val_fixture_is_skew_holdout():
    # a rotation of a VAL fixture is a legitimate held-out skew test -> goes to val
    reals = _reals('abcd')
    rot = _example(_a_val_fixture(reals), synthetic=True, transform='rotate')
    trainset, valset = datasets.split(reals + [rot], val_fraction=0.5)
    assert _has(valset, rot) and not _has(trainset, rot)


def test_crop_of_val_fixture_is_dropped():
    # a header-crop of a VAL fixture is synthetic CONTENT -> dropped, so content
    # validation stays real-pages-only
    reals = _reals('abcd')
    crop = _example(_a_val_fixture(reals), synthetic=True, transform='crop_top')
    trainset, valset = datasets.split(reals + [crop], val_fraction=0.5)
    assert not _has(trainset, crop) and not _has(valset, crop)


def test_train_fixture_synthetics_go_to_train():
    reals = _reals('abcd')
    val_fixtures = {e._fixture for e in datasets.split(reals, val_fraction=0.5)[1]}
    train_fixture = next(f'../fixtures/categorize/{c}.pdf' for c in 'abcd'
                         if f'../fixtures/categorize/{c}.pdf' not in val_fixtures)
    rot = _example(train_fixture, synthetic=True, transform='rotate')
    crop = _example(train_fixture, synthetic=True, transform='crop_top')
    trainset, valset = datasets.split(reals + [rot, crop], val_fraction=0.5)
    assert _has(trainset, rot) and _has(trainset, crop)
    assert not _has(valset, rot) and not _has(valset, crop)


def test_val_never_contains_synthetic_content():
    reals = _reals('abcd')
    vf = _a_val_fixture(reals)
    extras = [_example(vf, True, 'rotate'), _example(vf, True, 'crop_top')]
    _, valset = datasets.split(reals + extras, val_fraction=0.5)
    assert all(getattr(e, '_transform', None) != 'crop_top' for e in valset)


def test_subsample_spreads_across_fixtures_deterministically():
    examples = []
    for c in 'abcd':
        for _ in range(3):
            examples.append(_example(f'../fixtures/categorize/{c}.pdf'))
    picked = datasets.subsample(examples, 4)
    assert len(picked) == 4
    assert len({ex._fixture for ex in picked}) == 4
    assert [e._fixture for e in datasets.subsample(examples, 6)] == \
           [e._fixture for e in datasets.subsample(examples, 6)]
    assert datasets.subsample(examples, 999) is examples


def _record(image_name: str, **overrides) -> dict:
    record = {
        'image': f'images/{image_name}',
        'candidate_orientation': 'rows', 'contest_name_present': True,
        'candidate_names_present': True, 'headers_present': True,
        'precinct_scope': 'county', 'precinct_orientation': None,
        'skew_degrees': 0.0,
    }
    record.update(overrides)
    return record


def test_record_to_example_normalizes_and_marks_content():
    any_image = sorted(os.listdir(_IMAGES))[0]
    example = datasets.record_to_example(_record(any_image))
    assert example.precinct_orientation == 'none'   # null -> concrete literal
    assert example.eval_kind == 'content'
    assert 'image' in example.inputs()


def test_record_to_example_marks_rotation_as_skew():
    any_image = sorted(os.listdir(_IMAGES))[0]
    example = datasets.record_to_example(
        _record(any_image, transform='rotate', skew_degrees=1.5))
    assert example.eval_kind == 'skew'
    assert example.skew_degrees == 1.5
