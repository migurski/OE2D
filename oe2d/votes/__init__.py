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
import collections
import csv
import functools
import logging
import os
import re
import sys

import dotenv
import dspy
import pdfplumber

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


def _norm(text: str) -> str:
    '''Whitespace-and-case-insensitive key: a wrapped label can split mid-word across cells
    ("...and T" + "ER MAAT"), so match on the spaces removed entirely.'''
    return re.sub(r'\s+', '', (text or '')).lower()


def _parse_number(text: str) -> int | None:
    text = _clean(text).replace(',', '')            # canonical: no commas in totals
    return int(text) if re.fullmatch(r'-?\d+', text) else None


def _cell_count(cell: str) -> int | None:
    '''The vote count in a cell, or None. Table conversion sometimes MERGES a count with its
    percent into one cell ("1 100.00%"); the count is then the leading whitespace token. A
    pure-percent cell ("86.32%") has no integer leading token, so it is correctly skipped.'''
    token: str = _clean(cell).split(' ')[0].replace(',', '')
    return int(token) if re.fullmatch(r'-?\d+', token) else None


def _assign_methods(buckets: list[str], numbers: list[int]) -> dict | None:
    '''Map a candidate row's numeric cells onto its method buckets, robust to cell split/merge.

    Table conversion is not self-consistent within a document: a zero component may be dropped, or
    a value may split across cells, so the cell count can differ from the bucket count page to
    page. When counts match, zip in order. When a total bucket is present and a cell equals the sum
    of the others, that cell is the total and the rest fill the component buckets left-to-right
    (a dropped trailing component -- usually provisional -- becomes 0). Returns None if unalignable.
    '''
    store = lambda bucket: 'votes' if bucket == _TOTAL_BUCKET else bucket
    if len(numbers) == len(buckets):
        return {store(bucket): value for bucket, value in zip(buckets, numbers)}
    if _TOTAL_BUCKET in buckets and numbers:
        components: list[str] = [bucket for bucket in buckets if bucket != _TOTAL_BUCKET]
        for index, value in enumerate(numbers):
            others: list[int] = numbers[:index] + numbers[index + 1:]
            if value == sum(others):                      # a dropped (zero) trailing component
                return _record(components, others, value)
        # a spurious extra cell wedged in: the total still equals a leading run of components
        for index in range(len(numbers)):
            for take in range(min(index, len(components)), 0, -1):   # longest run first
                if numbers[index] == sum(numbers[:take]):
                    return _record(components, numbers[:take], numbers[index])
    return None


def _record(components: list[str], values: list[int], total: int) -> dict:
    '''Assemble a method record: components in order (missing trailing ones -> 0), plus votes.'''
    record: dict = {'votes': total}
    for bucket, value in zip(components, values):
        record[bucket] = value
    for bucket in components[len(values):]:
        record[bucket] = 0
    return record


def _count_columns(grid: list[list[str]], candidate_rows: list, want: int) -> list[int]:
    '''The columns that hold vote counts, by CONSENSUS across a page's candidate rows. The column
    structure is consistent within a page even when table conversion drifts across pages, so a
    stray cell wedged into one row (present in no other) is outvoted, while the real method columns
    -- shared by every candidate row -- win. Returns the `want` most-common count columns in order.'''
    frequency: dict[int, int] = collections.defaultdict(int)
    for role in candidate_rows:
        if role.row_index < len(grid):
            for column, cell in enumerate(grid[role.row_index]):
                if _cell_count(cell) is not None:
                    frequency[column] += 1
    ranked: list[int] = sorted(frequency, key=lambda column: (-frequency[column], column))
    return sorted(ranked[:want])


def _split_party(candidate: str, party: str) -> tuple[str, str]:
    '''Separate a trailing "(PARTY)" the interpreter sometimes leaves in the candidate name
    ("Kamala D. Harris (DEM)"). Inconsistent inclusion of it otherwise breaks candidate identity
    (grouping) and pollutes the emitted name. Uses it as the party only when none was given.'''
    match = re.search(r'\s*\(([^)]*)\)\s*$', candidate)
    name: str = re.sub(r'\s*\([^)]*\)\s*$', '', candidate).strip()
    if match and not party:
        party = match.group(1).strip()
    return name, party


def _contiguous_label(row: list[str], start: int) -> str:
    '''Join non-empty cells from column `start` until the first gap. A precinct name can wrap into
    the adjacent column ("Gettysburg" + "1"), while trailing junk (a registered-voters banner) sits
    past an empty cell -- stopping at the gap keeps the name and drops the banner.'''
    parts: list[str] = []
    for cell in row[start:]:
        text: str = _clean(cell)
        if not text:
            break
        parts.append(text)
    return ' '.join(parts)


def grid_to_text(rows: list[list[str]]) -> str:
    '''Render an extracted grid for the interpreter: one row per line, 0-based columns.'''
    return '\n'.join('%d: %s' % (i, ' | '.join(_clean(cell) for cell in row))
                     for i, row in enumerate(rows))


@functools.lru_cache(maxsize=None)
def _open_pdf(path: str) -> 'pdfplumber.PDF':
    return pdfplumber.open(path)


def read_text_grid(path: str, page: int, text_tolerance: int = 3) -> list[list[str]]:
    '''Vendor-adaptive read for precinct-major (Electionware-style) pages: text-alignment table
    reconstruction. source_table.page_table is tuned for the ruled Hart SOVCs and mis-reads these
    (ruled lines only bound fragments); text alignment recovers the full grid, and text_tolerance
    keeps numbers from splitting ("509" -> "50","9"). Kept in oe2d.votes -- specific to this step.'''
    pdf: pdfplumber.PDF = _open_pdf(path)
    if page < 1 or page > len(pdf.pages):
        return []
    tables = pdf.pages[page - 1].find_tables(
        {'vertical_strategy': 'text', 'horizontal_strategy': 'text', 'text_tolerance': text_tolerance})
    if not tables:
        return []
    return max(tables, key=lambda table: len(table.extract())).extract()


def build_interpreter() -> dspy.Module:
    '''Construct the page interpreter. A trained artifact, when present, fully governs (its
    saved prompt AND lm win); otherwise bind the stock inference LM (temperature 0). Turns on
    cmpnd tracing here so every path that runs the interpreter reports its LLM calls.'''
    _instrument()
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
        names: set[str] = {_norm(_split_party(c.candidate, c.party)[0])
                           for c in schema.columns if c.role == 'candidate'}
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
    page_schemas: list[tuple] = []
    for page in pages:
        rows: list[list[str]] = source_table.page_table(file_path, page) or []
        schema: signatures.PageSchema = interpret_page(interpreter, office, candidate_context, rows)
        page_schemas.append((schema, rows))

    # A first walk with each page's own skip labels, to learn the document's real precinct labels.
    first_blocks: list[list[dict]] = [walk_page(rows, schema) for schema, rows in page_schemas]
    precinct_labels: set[str] = {block['label'] for blocks in first_blocks
                                 for block in blocks if block['label']}

    # Consensus skip labels: a label is a total/header only if MULTIPLE pages call it one AND it is
    # not a fragment of any real precinct label. This drops a single page's mistake (a precinct name
    # in skip_labels collapses that group) and a common wrap fragment ("Precinct 1") that several
    # pages mis-skip -- while keeping a genuine total ("Barry County Michigan"), which never appears
    # inside a precinct name.
    skip_frequency: dict[str, int] = collections.defaultdict(int)
    for schema, _rows in page_schemas:
        for label in set(schema.skip_labels):
            skip_frequency[label] += 1
    consensus_skip: list[str] = [
        label for label, count in skip_frequency.items()
        if count >= 2 and not any(label in precinct for precinct in precinct_labels)]

    pages_schema_blocks: list[tuple] = []
    for schema, rows in page_schemas:
        schema.skip_labels = consensus_skip
        pages_schema_blocks.append((schema, walk_page(rows, schema)))
        logger.info('page: %d columns, %d precinct blocks', len(schema.columns), len(pages_schema_blocks[-1][1]))

    votes: dict = {}
    for group in _precinct_groups(pages_schema_blocks):
        # Precinct labels by consensus across the group's candidate-group pages: a page may drop the
        # first precinct's label to None, but a sibling page carries it.
        span: int = max(len(blocks) for _schema, blocks in group)
        labels: list = []
        for index in range(span):
            found = next((blocks[index]['label'] for _schema, blocks in group
                          if index < len(blocks) and blocks[index]['label']), None)
            labels.append(found)
        for index, precinct in enumerate(labels):
            for schema, blocks in group:
                if index >= len(blocks):
                    continue
                for column in schema.columns:
                    if column.role != 'candidate':
                        continue
                    candidate, party = _split_party(column.candidate, column.party)
                    for bucket, row in blocks[index]['methods'].items():
                        value = _parse_number(row[column.index]) if column.index < len(row) else None
                        if value is None:
                            continue
                        store: str = 'votes' if bucket == _TOTAL_BUCKET else bucket
                        votes.setdefault((precinct, candidate, party), {})[store] = value
    return votes


def build_precinct_interpreter() -> dspy.Module:
    '''The precinct-major (candidates-as-rows) interpreter. Instruments like build_interpreter.'''
    _instrument()
    interpreter: dspy.Module = dspy.Predict(signatures.InterpretPrecinctPage)
    if os.path.exists(OPTIMIZED_MODEL_PATH):
        interpreter.load(OPTIMIZED_MODEL_PATH)
    else:
        interpreter.set_lm(dspy.LM(LM_CLAUDE_SONNET45, temperature=0.0, max_tokens=4096))
    return interpreter


def extract_precinct_contest(file_path: str, pages: list[int], office: str, candidate_context: str,
                             interpreter: dspy.Module | None = None) -> dict:
    '''Extract a candidates-as-rows contest whose precincts are one-per-page (precinct in the page
    header, methods across columns). The document's pages are structurally identical, so interpret
    ONE sample page and apply that schema to every page -- one LLM call per document, then
    deterministic exact-label extraction. Candidate rows are found by their verbatim row-label
    (identical across pages); the precinct name is read from the learned header cell.'''
    interpreter = interpreter or build_precinct_interpreter()
    sample: list[list[str]] = read_text_grid(file_path, pages[0])
    prediction = interpreter(office=office, candidate_context=candidate_context,
                             grid=grid_to_text(sample))
    schema: signatures.PrecinctPageSchema = prediction.precinct_schema
    # The interpreter names the method buckets in left-to-right order; trust that order, not its
    # exact column indices (split/garbled headers throw those off). Code maps the buckets onto each
    # candidate row's actual numeric cells, so spacer columns and header noise don't matter.
    buckets: list[str] = [bucket for _index, bucket in sorted(schema.method_columns.items(),
                                                              key=lambda item: int(item[0]))]
    logger.info('learned precinct schema: %d candidate rows, method order %s, precinct at (%d,%d)',
                len(schema.candidate_rows), buckets, schema.precinct_row, schema.precinct_column)

    # Apply by ROW INDEX (from the sample), not by matching the row label: pages are structurally
    # identical, indices scope to the right contest when several stack on a page (their
    # write-in/over/under labels repeat), and it sidesteps labels that wrap mid-word across cells.
    votes: dict = {}
    for page in pages:
        grid: list[list[str]] = read_text_grid(file_path, page)
        precinct: str = ''
        if schema.precinct_row < len(grid):
            precinct = _contiguous_label(grid[schema.precinct_row], schema.precinct_column)
        count_columns: list[int] = _count_columns(grid, schema.candidate_rows, len(buckets))
        for role in schema.candidate_rows:
            if role.row_index >= len(grid):
                continue
            row: list[str] = grid[role.row_index]
            numbers: list[int] = [_cell_count(row[column]) for column in count_columns
                                  if column < len(row) and _cell_count(row[column]) is not None]
            record = _assign_methods(buckets, numbers)
            if record is None:
                logger.info('  %s / %s row %d: %d cells unalignable to %d buckets -- skipped',
                            precinct, role.candidate, role.row_index, len(numbers), len(buckets))
                continue
            candidate: str = re.sub(r':+$', '', role.candidate).strip()   # drop a trailing-colon artifact
            votes[(precinct, candidate, role.party)] = record
    return votes


def extract(file_path: str, pages: list[int], office: str, candidate_context: str,
            orientation: str = 'columns', interpreter: dspy.Module | None = None) -> dict:
    '''Dispatch on candidate orientation (from oe2d.pages): 'rows' -> precinct-major path,
    'columns' -> contest-major path.'''
    if orientation == 'rows':
        return extract_precinct_contest(file_path, pages, office, candidate_context, interpreter)
    return extract_contest(file_path, pages, office, candidate_context, interpreter)


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

    votes = extract_contest(args.path, _parse_pages(args.pages), args.office,
                            resolve_context(args.context))       # build_interpreter() instruments
    rows = votes_to_rows(votes, args.county, args.office, args.district)
    writer = csv.DictWriter(sys.stdout, CANON_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)


if __name__ == '__main__':
    main()
