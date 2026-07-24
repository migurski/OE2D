'''Load the per-page gold set into DSPy examples for GEPA optimization.

Reads oe2d-data/pages/labels.jsonl (one row per committed page image) and wraps
each as a dspy.Example whose single input is the page image and whose outputs are
the in-page properties.

Two splitting rules keep the evaluation honest:
- Split by SOURCE FIXTURE, not by page: pages from one fixture share vendor,
  contest, and skew, so a page-level split would leak. Whole fixtures go to one
  side or the other.
- SYNTHETIC rows (the rotate/crop augmentations) are TRAIN-ONLY. Validation is
  measured on real pages exclusively, so scores reflect real performance.
'''
from __future__ import annotations

import collections
import json
import os

import dspy

from . import OUTPUT_FIELDS

# labels.jsonl lives beside the images under the top-level oe2d-data tree (not in
# the wheel); resolve it and the image paths against the repo root, two levels up
# from this package (oe2d/pages -> oe2d -> repo).
_REPO_ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PAGES_DIR: str = os.path.join(_REPO_ROOT, 'oe2d-data', 'pages')
_LABELS_PATH: str = os.path.join(_PAGES_DIR, 'labels.jsonl')

INPUT_FIELDS: tuple[str, ...] = ('image',)


def load_records(labels_path: str = _LABELS_PATH) -> list[dict]:
    '''Read labels.jsonl into a list of dicts, one per page image.'''
    records: list[dict] = []
    with open(labels_path, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def image_path(record: dict) -> str:
    '''Absolute path to a record's committed page image.'''
    return os.path.join(_PAGES_DIR, record['image'])


def record_to_example(record: dict) -> dspy.Example:
    '''Build one dspy.Example: the page image in, the in-page properties out.

    A null precinct_orientation in the gold data is normalized to 'none' to match
    the signature's Literal, which has no null member.

    eval_kind marks how the metric scores this example: a rotate augmentation
    (which carries a real known angle) is scored on skew only; every other page
    (real, or a header-crop) is scored on the content fields only, and its skew
    (0.0 for vector, null for scanned) is ignored. It is a FIELD, not a private
    attribute, so it survives through GEPA into the metric call.
    '''
    fields: dict = {'image': dspy.Image(image_path(record))}
    for name in OUTPUT_FIELDS:
        value = record.get(name)
        if name == 'precinct_orientation' and value is None:
            value = 'none'
        fields[name] = value
    fields['eval_kind'] = 'skew' if record.get('transform') == 'rotate' else 'content'
    return dspy.Example(**fields).with_inputs(*INPUT_FIELDS)


def load_examples(labels_path: str = _LABELS_PATH) -> list[dspy.Example]:
    '''Load every gold record whose image exists as a dspy.Example.'''
    examples: list[dspy.Example] = []
    for record in load_records(labels_path):
        if not os.path.exists(image_path(record)):
            continue
        example = record_to_example(record)
        example._synthetic = bool(record.get('synthetic'))
        example._transform = record.get('transform')       # 'rotate' | 'crop_top' | None
        example._fixture = record.get('source_fixture', record['image'])
        examples.append(example)
    return examples


def split(examples: list[dspy.Example], val_fraction: float = 0.25) -> tuple[list[dspy.Example], list[dspy.Example]]:
    '''Split into train/val by source fixture, routing each kind of page.

    Real pages are grouped by fixture; the fixtures are sorted and every
    round(1/val_fraction)-th one is a validation fixture.

    - Real (content) pages: val fixtures -> val, train fixtures -> train.
    - Rotate augmentations (skew examples): a rotation of a VAL fixture's page
      goes to VAL as the skew holdout (a legitimate held-out skew test — skew is
      geometric, not learnable from the page's content); a rotation of a train
      fixture goes to train. Rotations of dropped fixtures (e.g. removed by a
      --max-examples subsample) are dropped.
    - Crop augmentations (content examples): train fixtures only. A crop of a val
      fixture is dropped so the CONTENT validation stays real-pages-only (no
      synthetic content variants inflating the content metric).

    Deterministic — no random state.
    '''
    real: list[dspy.Example] = [ex for ex in examples if not getattr(ex, '_synthetic', False)]
    rotates: list[dspy.Example] = [ex for ex in examples if getattr(ex, '_transform', None) == 'rotate']
    crops: list[dspy.Example] = [ex for ex in examples if getattr(ex, '_transform', None) == 'crop_top']

    by_fixture: dict[str, list[dspy.Example]] = collections.defaultdict(list)
    for example in real:
        by_fixture[getattr(example, '_fixture')].append(example)

    stride: int = max(2, round(1 / val_fraction))
    val_fixtures: set[str] = set()
    trainset: list[dspy.Example] = []
    valset: list[dspy.Example] = []
    for index, fixture in enumerate(sorted(by_fixture)):
        if index % stride == 0:
            val_fixtures.add(fixture)
            valset.extend(by_fixture[fixture])
        else:
            trainset.extend(by_fixture[fixture])
    train_fixtures: set[str] = set(by_fixture) - val_fixtures

    for example in rotates:
        fixture = getattr(example, '_fixture', None)
        if fixture in val_fixtures:
            valset.append(example)          # skew holdout
        elif fixture in train_fixtures:
            trainset.append(example)
    for example in crops:
        if getattr(example, '_fixture', None) in train_fixtures:
            trainset.append(example)
    return trainset, valset


def subsample(examples: list[dspy.Example], n: int) -> list[dspy.Example]:
    '''Deterministically take up to n examples, spread across source fixtures.

    Round-robins across the fixtures (sorted) so a small slice still spans as many
    vendors/layouts as possible. Used for a quick optimization pass; run it on the
    real examples before split() so validation keeps real pages from several
    fixtures.
    '''
    if n >= len(examples):
        return examples
    by_fixture: dict[str, list[dspy.Example]] = collections.defaultdict(list)
    for example in examples:
        by_fixture[getattr(example, '_fixture', '')].append(example)
    order: list[str] = sorted(by_fixture)
    picked: list[dspy.Example] = []
    depth: int = 0
    while len(picked) < n:
        advanced: bool = False
        for fixture in order:
            group: list[dspy.Example] = by_fixture[fixture]
            if depth < len(group):
                picked.append(group[depth])
                advanced = True
                if len(picked) >= n:
                    break
        if not advanced:
            break
        depth += 1
    return picked


def load_split(labels_path: str = _LABELS_PATH, val_fraction: float = 0.25) -> tuple[list[dspy.Example], list[dspy.Example]]:
    '''Convenience: load examples and split them in one call.'''
    return split(load_examples(labels_path), val_fraction=val_fraction)
