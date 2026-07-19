'''Tests for the GEPA dataset loader (hermetic; a tiny temp gold set).

The real loader recomputes container/page_count from every fixture, which is
slow across the whole corpus. These tests point it at a handful of the small
source_table fixtures instead, so they exercise the logic without opening 88
files.
'''
import json
import os

import pytest

from oe2d import categorize
from oe2d.categorize import datasets

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_SMALL_FIXTURES = os.path.join(_REPO_ROOT, 'oe2d-data', 'fixtures', 'source_table')


def _record(name: str, container_hint: str, **overrides) -> dict:
    fields = {
        'path': os.path.join(_SMALL_FIXTURES, name),
        'container': container_hint,
        'orientation': 'candidate_columns', 'grain': 'precinct',
        'has_rotated_headers': False, 'has_stacked_contests': False,
        'has_side_by_side': False, 'has_multi_sheet_stitch': False,
    }
    fields.update(overrides)
    return fields


@pytest.fixture
def gold_file(tmp_path):
    '''A small gold JSONL with two containers, two examples each.'''
    records = [
        _record('glenn-pdf-p15.pdf', 'vector_pdf', orientation='candidate_columns'),
        _record('glenn-pdf-p15.pdf', 'vector_pdf', orientation='candidate_rows'),
        _record('sf-xlsx-sheet2.xlsx', 'xlsx', grain='precinct'),
        _record('sf-xlsx-sheet2.xlsx', 'xlsx', grain='unknown'),
    ]
    path = tmp_path / 'category.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in records) + '\n', encoding='utf-8')
    return str(path)


def test_load_examples_recomputes_inputs(gold_file):
    examples = datasets.load_examples(gold_file)
    assert len(examples) == 4
    for example in examples:
        for name in datasets.INPUT_FIELDS:
            assert example.get(name) is not None
        # container is recomputed from the file, not taken from the record.
        assert example.container in categorize.CONTAINERS


def test_examples_carry_output_fields(gold_file):
    example = datasets.load_examples(gold_file)[0]
    assert example.orientation is not None
    assert example.grain is not None
    for name in categorize.LAYOUT_PROPERTIES:
        assert isinstance(example.get(name), bool)


def test_split_is_deterministic_and_covers_both_sides(gold_file):
    examples = datasets.load_examples(gold_file)
    train_a, val_a = datasets.split(examples)
    train_b, val_b = datasets.split(examples)
    assert [ex.file_path for ex in train_a] == [ex.file_path for ex in train_b]
    assert [ex.file_path for ex in val_a] == [ex.file_path for ex in val_b]
    assert train_a and val_a
    assert len(train_a) + len(val_a) == len(examples)


def test_split_stratifies_each_container(gold_file):
    examples = datasets.load_examples(gold_file)
    _, valset = datasets.split(examples)
    # Both containers have two examples, so both must reach validation.
    assert {ex.container for ex in valset} == {'vector_pdf', 'xlsx'}
