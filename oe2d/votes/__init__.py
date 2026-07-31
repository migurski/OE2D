'''Extract OpenElections precinct rows from a located contest's pages.

Given a source file and the pages `oe2d.contests` located for one contest -- plus the office,
district, and expected candidates it surfaced -- read the vote table and emit canonical precinct
rows: county, precinct, office, district, party, candidate, votes[, method breakdown]. Precincts
often span several pages, split by candidate columns (horizontal) and by precinct rows
(vertical); this stitches them back together.

Design: the LLM decides structure, deterministic code moves the digits (never the reverse). The
interpreter (signatures.InterpretResultsPage) reads each page's grid and returns a schema -- which
column holds precinct/method labels, how to read each data column, which row labels denote a vote
method or a total/header to skip -- and a generic walker follows those indices, holding no English
of its own. The read path is oe2d.source_table for vector PDFs / spreadsheets (scans need Textract,
handled upstream). Credentials come from the environment; the interpreter LM is AWS Bedrock.

Usage: oe2d-votes file.pdf --pages 7-12 --office President \
           --context @candidates.txt --county Oscoda
'''
from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import sys

import dotenv
import dspy

from .. import source_table
from . import signatures

logger: logging.Logger = logging.getLogger(__name__)

OPTIMIZED_MODEL_PATH: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'model', 'optimized_vote_extractor.json')

# The interpreter LM: AWS Bedrock Claude Sonnet, kept beside the program (not in a shared config).
# litellm reads AWS creds from the environment (AWS_PROFILE / keys); set AWS_REGION_NAME.
LM_CLAUDE_SONNET45: str = 'bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0'

# Canonical OpenElections precinct columns (see votes-HANDOFF.md).
CANON_COLUMNS: tuple[str, ...] = (
    'county', 'precinct', 'office', 'district', 'party', 'candidate',
    'votes', 'election_day', 'early_voting', 'absentee_mail', 'provisional')

# A method label maps to 'total' for the grand-total column/row; we store that under 'votes'.
_TOTAL_BUCKET: str = 'total'


def _clean(text: str | None) -> str:
    return re.sub(r'\s+', ' ', (text or '').strip())


def _parse_number(text: str) -> int | None:
    text = _clean(text).replace(',', '')            # canonical: no commas in totals
    return int(text) if re.fullmatch(r'-?\d+', text) else None


def grid_to_text(rows: list[list[str]]) -> str:
    '''Render an extracted grid for the interpreter: one row per line, 0-based columns.'''
    return '\n'.join('%d: %s' % (i, ' | '.join(_clean(cell) for cell in row))
                     for i, row in enumerate(rows))


def build_interpreter() -> dspy.Module:
    '''Construct the page interpreter. A trained artifact, when present, fully governs (its
    saved prompt AND lm win); otherwise bind the stock inference LM (temperature 0).'''
    interpreter: dspy.Module = dspy.Predict(signatures.InterpretResultsPage)
    if os.path.exists(OPTIMIZED_MODEL_PATH):
        interpreter.load(OPTIMIZED_MODEL_PATH)
    else:
        interpreter.set_lm(dspy.LM(LM_CLAUDE_SONNET45, temperature=0.0, max_tokens=4096))
    return interpreter


def interpret_page(interpreter: dspy.Module, office: str, candidate_context: str,
                   rows: list[list[str]]) -> signatures.PageSchema:
    '''Interpret one page's grid into a PageSchema (the LLM step; no numbers read).'''
    prediction = interpreter(office=office, candidate_context=candidate_context,
                             grid=grid_to_text(rows))
    return prediction.page_schema


def walk_page(rows: list[list[str]], schema: signatures.PageSchema) -> list[dict]:
    '''Ordered precinct blocks on a page, driven entirely by the schema (no English here).

    Each block is {'label': str | None, 'methods': {bucket: row}}. A block accumulates its
    precinct label (which may wrap across rows) then its vote-method rows; a skip label ends the
    page once real blocks exist (a terminal total section) or is ignored as a leading header.
    '''
    label_column: int = schema.label_column
    blocks: list[dict] = []
    label_parts: list[str] = []
    methods: dict[str, list[str]] = {}

    def flush() -> None:
        if methods:
            blocks.append({'label': ' '.join(label_parts) or None, 'methods': methods.copy()})

    for row in rows[schema.first_data_row:]:
        label: str = _clean(row[label_column]) if len(row) > label_column else ''
        if label in schema.method_labels:
            methods[schema.method_labels[label]] = row
        elif label == '':
            continue
        elif label in schema.skip_labels:
            if blocks or methods:               # a terminal total/junk section -> stop the page
                break
            continue                            # a leading header -> skip
        else:                                   # a precinct label (may wrap across rows)
            if methods:
                flush()
                label_parts, methods = [], {}
            label_parts.append(label)
    flush()
    return blocks


def _precinct_groups(pages_schema_blocks: list[tuple]) -> list[list[tuple]]:
    '''Partition (schema, blocks) pages into precinct-groups. Within a precinct-group the
    candidate-group pages carry DISJOINT candidate columns for the same precincts; a page that
    repeats a candidate already seen in the current group starts a new precinct-group.'''
    groups: list[list[tuple]] = []
    current: list[tuple] = []
    seen: set[str] = set()
    for schema, blocks in pages_schema_blocks:
        names: set[str] = {c.candidate for c in schema.columns if c.role == 'candidate'}
        if current and (seen & names):
            groups.append(current)
            current, seen = [], set()
        current.append((schema, blocks))
        seen |= names
    if current:
        groups.append(current)
    return groups


def extract_contest(file_path: str, pages: list[int], office: str, candidate_context: str,
                    interpreter: dspy.Module | None = None) -> dict:
    '''Read the contest's pages and stitch them into votes[(precinct, candidate, party)][bucket].

    Reads each page (source_table), interprets it (LLM), walks it into ordered precinct blocks,
    partitions the pages into precinct-groups, then within a group concatenates candidate columns
    across the candidate-group pages by precinct position and across groups appends the precinct
    lists. The interpreter never touches a number; this function moves them.
    '''
    interpreter = interpreter or build_interpreter()
    pages_schema_blocks: list[tuple] = []
    for page in pages:
        rows: list[list[str]] = source_table.page_table(file_path, page) or []
        schema: signatures.PageSchema = interpret_page(interpreter, office, candidate_context, rows)
        pages_schema_blocks.append((schema, walk_page(rows, schema)))
        logger.info('page %d: %d columns, %d precinct blocks',
                    page, len(schema.columns), len(pages_schema_blocks[-1][1]))

    votes: dict = {}
    for group in _precinct_groups(pages_schema_blocks):
        labels: list = [block['label'] for block in group[0][1]]     # labels from candidate-group 1
        for index, precinct in enumerate(labels):
            for schema, blocks in group:
                if index >= len(blocks):
                    continue
                for column in schema.columns:
                    if column.role != 'candidate':
                        continue
                    for bucket, row in blocks[index]['methods'].items():
                        value = _parse_number(row[column.index]) if column.index < len(row) else None
                        if value is None:
                            continue
                        store: str = 'votes' if bucket == _TOTAL_BUCKET else bucket
                        key = (precinct, column.candidate, column.party)
                        votes.setdefault(key, {})[store] = value
    return votes


def votes_to_rows(votes: dict, county: str, office: str, district: str = '') -> list[dict]:
    '''Canonical precinct rows from a stitched votes mapping.'''
    rows: list[dict] = []
    for (precinct, candidate, party), buckets in votes.items():
        row: dict = {column: '' for column in CANON_COLUMNS}
        row.update(county=county, precinct=precinct, office=office, district=district,
                   party=party, candidate=candidate)
        for bucket, value in buckets.items():
            row[bucket] = value
        rows.append(row)
    return rows


def _instrument() -> None:
    '''Turn on cmpnd tracing when a key is configured (tag oe2d-votes).'''
    dotenv.load_dotenv()
    key: str | None = os.environ.get('CMPND_API_KEY')
    if not key:
        return
    try:
        import cmpnd
        cmpnd.configure(api_key=key, endpoint=os.environ.get('CMPND_ENDPOINT'),
                        project_tags=['oe2d-votes'])
        cmpnd.auto_instrument()
    except Exception:
        pass


def _parse_pages(spec: str) -> list[int]:
    '''Parse a --pages spec like "7-12" or "1,6,11" into a sorted page list.'''
    pages: set[int] = set()
    for part in spec.split(','):
        part = part.strip()
        if '-' in part:
            lo, hi = part.split('-', 1)
            pages.update(range(int(lo), int(hi) + 1))
        elif part:
            pages.add(int(part))
    return sorted(pages)


def resolve_context(spec: str) -> str:
    '''Resolve a --context value. An @-prefixed value reads the named file; else verbatim.'''
    if not spec.startswith('@'):
        return spec
    try:
        with open(spec[1:], encoding='utf-8') as handle:
            return handle.read()
    except OSError as error:
        raise SystemExit('cannot read --context file %r: %s' % (spec[1:], error))


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='Extract OpenElections precinct rows from a located contest.')
    parser.add_argument('path', help='Source file (vector PDF / spreadsheet)')
    parser.add_argument('--pages', required=True, help='Contest pages, e.g. 7-12 or 1,6,11')
    parser.add_argument('--office', required=True, help='OE office label, e.g. President')
    parser.add_argument('--district', default='', help='District, when the office has one')
    parser.add_argument('--county', required=True, help='County name (no "County" suffix)')
    parser.add_argument('--context', default='',
                        help='Expected candidates as "Name (PARTY)" lines; @path to read from a file')
    parser.add_argument('-v', '--verbose', action='store_true')
    args: argparse.Namespace = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format='%(message)s')
        logging.getLogger('oe2d').setLevel(logging.INFO)

    _instrument()
    votes = extract_contest(args.path, _parse_pages(args.pages), args.office,
                            resolve_context(args.context))
    rows = votes_to_rows(votes, args.county, args.office, args.district)
    writer = csv.DictWriter(sys.stdout, CANON_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)


if __name__ == '__main__':
    main()
