'''Load the vote-extraction gold set (oe2d-data/votes/).

Each gold example is an index.jsonl record (metadata: office, district, source_url, the contest
pages, container, geometry/schema features, checksums, electoral_context) plus a canonical
`<county>__<contest>__expected.csv` of the rows the extractor must reproduce. The numbers are
copied from human-authored state-repo CSVs -- they are the ground truth, not re-derived from the
PDFs. Sources live remotely (openelections-sources-*); fetch_source downloads and caches one.
'''
from __future__ import annotations

import csv
import json
import os
import urllib.request

import dspy

from .. import config

# The forward()/metric contract: these inputs go into the extractor, .rows is the scored output.
INPUT_FIELDS: tuple[str, ...] = (
    'file_path', 'pages', 'office', 'electoral_context', 'county', 'district',
    'orientation', 'read_strategy')

_REPO_ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DATA_DIR: str = os.path.join(_REPO_ROOT, 'oe2d-data', 'votes')
_INDEX_PATH: str = os.path.join(_DATA_DIR, 'index.jsonl')
_CACHE_DIR: str = config.SOURCE_CACHE_DIR


def load_index(path: str = _INDEX_PATH) -> list[dict]:
    '''Every gold record (metadata only).'''
    with open(path, encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def find(id_substring: str) -> dict:
    '''The single gold record whose id contains id_substring (error if not exactly one).'''
    matches: list[dict] = [r for r in load_index() if id_substring in r['id']]
    if len(matches) != 1:
        raise SystemExit('want exactly one gold id matching %r, found %d' % (id_substring, len(matches)))
    return matches[0]


def expected_rows(record: dict) -> list[dict]:
    '''The canonical gold rows for a record.'''
    path: str = os.path.join(_DATA_DIR, record['expected_csv'])
    with open(path, encoding='utf-8') as handle:
        return list(csv.DictReader(handle))


def electoral_context(record: dict) -> str:
    '''The expected-candidate prose supplied to the interpreter, one "Name (PARTY)" line per
    distinct candidate.

    Prefer the record's stored `electoral_context` strings -- the real production context looked up
    from the candidates/ directory (federal races) or period sources (older cycles). Fall back to
    DERIVING the list from the expected-answer CSV when a record has none; that baseline is idealized
    (it always names exactly the candidates in the answer), so a stored real-world list is what the
    eval and production actually use.

    Deliberately does NOT string-classify rows into candidate vs write-in/vote-integrity -- that
    is language interpretation, the LLM's job, not Python's.'''
    stored = record.get('electoral_context')
    if stored:
        items: list[str] = stored if isinstance(stored, list) else [stored]
        return 'Expected candidates in this contest:\n' + '\n'.join('- ' + s for s in items)
    lines: list[str] = []
    seen: set[str] = set()
    for row in expected_rows(record):
        name, party = row['candidate'], row['party']
        if name in seen:
            continue
        seen.add(name)
        lines.append('%s (%s)' % (name, party) if party else name)
    return 'Expected candidates in this contest:\n' + '\n'.join('- ' + line for line in lines)


def fetch_source(record: dict) -> str:
    '''Download the record's source file to the shared source cache (once) and return its path. Named
    by a readable slug of the file plus a hash of the SOURCE URL (config.source_cache_name), not the
    contest id, so the several contests that share one source file (e.g. Branch's four races) download
    and store ONE copy -- which also lets them share the content-keyed Textract cache.'''
    os.makedirs(_CACHE_DIR, exist_ok=True)
    path: str = os.path.join(_CACHE_DIR, config.source_cache_name(record['source_url']))
    if not os.path.exists(path):
        urllib.request.urlretrieve(record['source_url'], path)
    return path


def orientation(record: dict) -> str:
    '''The record's CONTENT structure: 'rows' (precinct-major) or 'columns' (contest-major).'''
    return record.get('geometry', {}).get('candidate_orientation', 'columns')


def read_strategy(record: dict) -> str:
    '''The record's READ MECHANICS: 'ruled_scan' (Textract TABLES) or 'auto' (one grid per page).'''
    return record.get('read_strategy') or 'auto'


def district(record: dict) -> str:
    '''The record's district as a string ('' when the office has none). The gold stores a missing
    district as null or an empty list; only a real string district is passed through.'''
    value = record.get('district')
    return value if isinstance(value, str) else ''


def record_to_example(record: dict) -> dspy.Example:
    '''Build one dspy.Example for GEPA/evaluate: the extractor inputs in, the gold rows out.

    The source is fetched (cached) so file_path is a local path the reader can open. The gold rows
    keep their canonical string form (the extractor emits ints for votes, but the metric coerces
    both sides to trimmed strings, so they compare cleanly).'''
    fields: dict = {
        'file_path': fetch_source(record),
        'pages': record['pages'],
        'office': record['office'],
        'electoral_context': electoral_context(record),
        'county': record['county'],
        'district': district(record),
        'orientation': orientation(record),
        'read_strategy': read_strategy(record),
        'rows': expected_rows(record),
    }
    example: dspy.Example = dspy.Example(**fields).with_inputs(*INPUT_FIELDS)
    example._id = record['id']
    example._container = record.get('container', '')
    return example


def load_examples(path: str = _INDEX_PATH, fetch: bool = True) -> list[dspy.Example]:
    '''Every gold record as a dspy.Example. fetch=False skips downloading sources (metadata-only
    examples, e.g. to inspect the split) and leaves file_path unset.'''
    examples: list[dspy.Example] = []
    for record in load_index(path):
        if fetch:
            examples.append(record_to_example(record))
        else:
            fields: dict = {name: None for name in INPUT_FIELDS}
            fields.update(office=record['office'], county=record['county'], pages=record['pages'],
                          district=district(record), orientation=orientation(record),
                          read_strategy=read_strategy(record), rows=expected_rows(record))
            example: dspy.Example = dspy.Example(**fields).with_inputs(*INPUT_FIELDS)
            example._id = record['id']
            example._container = record.get('container', '')
            examples.append(example)
    return examples


def split(examples: list[dspy.Example], val_fraction: float = 0.3) -> tuple[list[dspy.Example], list[dspy.Example]]:
    '''Split into train/val deterministically by county, so a county's contests never straddle the
    split (they share vendor, layout, and gold quirks -- a per-contest split would leak). Counties are
    sorted and every round(1/val_fraction)-th one is held out for validation.'''
    import collections
    by_county: dict[str, list[dspy.Example]] = collections.defaultdict(list)
    for example in examples:
        by_county[getattr(example, 'county', '') or ''].append(example)
    stride: int = max(2, round(1 / val_fraction))
    trainset: list[dspy.Example] = []
    valset: list[dspy.Example] = []
    for index, county in enumerate(sorted(by_county)):
        (valset if index % stride == 0 else trainset).extend(by_county[county])
    return trainset, valset


def load_split(path: str = _INDEX_PATH, val_fraction: float = 0.3) -> tuple[list[dspy.Example], list[dspy.Example]]:
    '''Convenience: load examples and split them by county in one call.'''
    return split(load_examples(path), val_fraction=val_fraction)
