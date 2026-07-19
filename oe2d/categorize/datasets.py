'''Load the categorization gold set into DSPy examples for GEPA optimization.

Reads labels/category.jsonl, recomputes the deterministic inputs (container,
page_count) from each fixture so the training inputs never drift from what the
CLI actually feeds the RLM, and wraps each row as a dspy.Example with the
judgment fields (orientation, grain, the four has_*) as the expected outputs.

The train/val split is deterministic (sorted by path, no random module) and
stratified by container so rare shapes (docx, csv, zip, xls_binary) land on both
sides rather than all in one.
'''
from __future__ import annotations

import collections
import json
import os

import dspy

from .. import categorize

# The gold JSONL stores repo-relative paths; resolve them against the repo root,
# which is two levels up from this package (oe2d/categorize -> oe2d -> repo).
_REPO_ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_GOLD_PATH: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'labels', 'category.jsonl')

# Inputs the RLM receives; outputs it must predict (the rest are deterministic).
INPUT_FIELDS: tuple[str, ...] = ('file_path', 'container', 'page_count')
OUTPUT_FIELDS: tuple[str, ...] = ('orientation', 'grain') + categorize.LAYOUT_PROPERTIES


def resolve_path(record_path: str) -> str:
    '''Turn a gold record's repo-relative path into an absolute fixture path.'''
    if os.path.isabs(record_path):
        return record_path
    return os.path.join(_REPO_ROOT, record_path)


def load_records(gold_path: str = _GOLD_PATH) -> list[dict]:
    '''Read the gold JSONL into a list of dicts, one per labeled fixture.'''
    records: list[dict] = []
    with open(gold_path, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def record_to_example(record: dict) -> dspy.Example:
    '''Build one dspy.Example, recomputing container/page_count from the file.'''
    path: str = resolve_path(record['path'])
    container: str = categorize.detect_container(path)
    page_count: int = categorize.count_pages(path, container)

    fields: dict = {
        'file_path': path,
        'container': container,
        'page_count': page_count,
        'orientation': record['orientation'],
        'grain': record['grain'],
    }
    for name in categorize.LAYOUT_PROPERTIES:
        fields[name] = bool(record.get(name, False))

    return dspy.Example(**fields).with_inputs(*INPUT_FIELDS)


def load_examples(gold_path: str = _GOLD_PATH) -> list[dspy.Example]:
    '''Load every gold record as a dspy.Example, skipping missing fixtures.'''
    examples: list[dspy.Example] = []
    for record in load_records(gold_path):
        if os.path.exists(resolve_path(record['path'])):
            examples.append(record_to_example(record))
    return examples


def split(examples: list[dspy.Example], val_fraction: float = 0.3) -> tuple[list[dspy.Example], list[dspy.Example]]:
    '''Split into train/val deterministically, stratified by container.

    Within each container group the fixtures are sorted by path and every
    round(1/val_fraction)-th one goes to validation, so rare containers still
    contribute to both sides and the split never depends on random state.
    '''
    by_container: dict[str, list[dspy.Example]] = collections.defaultdict(list)
    for example in examples:
        by_container[example.container].append(example)

    stride: int = max(2, round(1 / val_fraction))
    trainset: list[dspy.Example] = []
    valset: list[dspy.Example] = []
    for container in sorted(by_container):
        group: list[dspy.Example] = sorted(by_container[container], key=lambda ex: ex.file_path)
        for index, example in enumerate(group):
            (valset if index % stride == 0 else trainset).append(example)
    return trainset, valset


def load_split(gold_path: str = _GOLD_PATH, val_fraction: float = 0.3) -> tuple[list[dspy.Example], list[dspy.Example]]:
    '''Convenience: load examples and split them in one call.'''
    return split(load_examples(gold_path), val_fraction=val_fraction)
