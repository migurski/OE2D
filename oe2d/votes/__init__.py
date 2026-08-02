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
import typing

import dotenv
import dspy
import pdfplumber

from .. import source_table
from . import signatures

logger: logging.Logger = logging.getLogger(__name__)

# How to turn a contest's pages into grids -- READ MECHANICS ONLY. 'auto' picks the reader from what
# each page offers (ruled vector, rotated text-aligned, or a scan with no text layer, one grid per
# page). 'flat_tables' reads a page as a set of flat candidate-column tables and scopes the contest
# across them by header-match -- the content structure Huron and Branch share; the table reader is
# picked by the page (Textract TABLES for a scan, pdfplumber text-strategy for a borderless vector
# page). 'ruled_scan' is the scan-only spelling of the same flat path (kept for existing gold). This
# is orthogonal to CONTENT structure -- candidate orientation, and whether a table is flat (one row
# per precinct) or has vote-method sub-rows, are decided from the interpreted content.
ReadStrategy = typing.Literal['auto', 'ruled_scan', 'flat_tables']

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

# All write-in columns/rows (the LLM flags them; sources split them to excruciating detail) are
# summed into this one consolidated row. The label is our output constant, not a document term.
WRITE_IN_LABEL: str = 'Write-ins'


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


def _consolidate_write_in(totals: list[int], components: list[int]) -> int:
    '''Combine one precinct+method's write-in figures into a single value. The interpreter marks
    which columns are an explicit AGGREGATE write-in total (write_in_total) versus components. When
    a real total is present it already sums the itemized write-ins, so use it and do not add the
    components (a total-plus-breakdown, e.g. Adams "Write-In Totals" over "Not Assigned"). Otherwise
    the write-in columns are separate components -- a scattered/unresolved "Write-in" line plus the
    named qualified write-ins -- and they add together (e.g. Barry scattered 6 + Sonski 2 = 8).

    Keeping the total-vs-component call in the interpreter (language) instead of guessing it from the
    numbers avoids the failure where a scattered value happens to equal a qualified value per method
    and a numeric "is one the sum of the others" test wrongly collapses them.'''
    if totals:
        return max(totals)
    return sum(components)


def _reconciles(votes: dict, totals: dict) -> bool:
    '''True when the extracted precinct rows sum to the printed county totals per candidate column.

    The confirm for a proposed ruled_scan (Textract TABLES) read: for each candidate column, add up
    that column across every extracted precinct row and compare to the printed county-total row. A
    clean segmentation includes each precinct once with columns aligned, so the sums match. The
    failure modes all break it by a large margin -- dropped precincts (sum too low), duplicated rows
    (too high), split columns (both wrong), or method-sub-row content mis-read as flat (every column
    roughly doubles). We require a MAJORITY of the candidate columns to match exactly, so one OCR
    digit slip in a single cell doesn't veto an otherwise-clean read, while a structural failure --
    which throws off every column at once -- still fails. No printed totals to check against (they can
    sit on a page outside the contest range) -> cannot confirm -> False, and the caller then prefers
    the cheap read rather than gambling on TABLES.'''
    if not totals:
        return False
    sums: dict = collections.defaultdict(int)
    for (precinct, candidate, party), buckets in votes.items():
        if candidate == WRITE_IN_LABEL:
            continue
        sums[(candidate, party)] += buckets.get('votes') or 0
    matched: int = sum(1 for key, printed in totals.items() if sums.get(key, 0) == printed)
    return matched * 2 > len(totals)


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


# Common English bigrams -- enough to tell, cheaply and deterministically, whether a run of text
# reads more like English forwards or reversed. Used only to decide page ORIENTATION (is this a
# rotated SOVC whose header text came out mirrored?); the candidate/terminology matching still
# happens in the LLM interpreter.
_COMMON_BIGRAMS: frozenset = frozenset((
    'th he in er an re on at en nd ti es or te of ed is it al ar st to nt ng se ha as ou io le ve co '
    'me de hi ri ro ic ne ea ra ce li ch ll be ma si om ur ca el ta la ns').split())


def _bigram_score(text: str) -> int:
    letters: str = re.sub(r'[^a-z]', '', text.lower())
    return sum(letters[i:i + 2] in _COMMON_BIGRAMS for i in range(len(letters) - 1))


def _reads_better_reversed(tokens: list[str]) -> bool:
    '''True when the tokens, as a set, score higher reversed than forward -- i.e. the text layer is
    mirrored (a 90deg-rotated header extracted character-reversed, "acisseJ" for "Jessica").'''
    forward: int = sum(_bigram_score(token) for token in tokens)
    reverse: int = sum(_bigram_score(token[::-1]) for token in tokens)
    return reverse > forward


def read_rotated_grid(path: str, page: int, x_gap: float = 15, y_tol: float = 3) -> list[list[str]]:
    '''Read a rotated-header text-aligned SOVC page (e.g. Calhoun MI) into a grid.

    These pages have no ruled lines (so source_table.page_table finds no columns) and their column
    HEADERS are rotated 90deg, which the text layer emits character-reversed and word-scrambled
    ("acisseJ ztrawS )MED(" for "Jessica Swartz (DEM)"). The vote NUMBERS are upright and read fine.
    We recover columns from geometry: cluster the rotated header words into candidate columns by an
    x-gap, un-mirror each token (only when the page as a whole reads better reversed), and bin the
    upright body words into those columns by x. The precinct name is duplicated across a left
    (statistics) block and a right (votes) block; the left copy sits in the label column and is
    read as-is. Returns a normal grid the columns pipeline consumes -- no OCR (the text layer is
    present, just mirrored).'''
    pdf: pdfplumber.PDF = _open_pdf(path)
    if page < 1 or page > len(pdf.pages):
        return []
    words: list[dict] = pdf.pages[page - 1].extract_words(extra_attrs=['upright'])
    rotated: list[dict] = [w for w in words if not w.get('upright', True)]
    upright: list[dict] = [w for w in words if w.get('upright', True)]
    if not rotated:
        return []
    unmirror = (lambda t: t[::-1]) if _reads_better_reversed([w['text'] for w in rotated]) else (lambda t: t)

    # cluster rotated header words into columns by an x-gap between adjacent x0
    rotated.sort(key=lambda w: w['x0'])
    bands: list[list[dict]] = []
    for word in rotated:
        if bands and word['x0'] - bands[-1][-1]['x0'] > x_gap:
            bands.append([])
        elif not bands:
            bands.append([])
        bands[-1].append(word)
    band_defs: list[list] = []                       # [lo, hi, header_name]
    for band in bands:
        xs: list[float] = [w['x0'] for w in band]
        name: str = ' '.join(unmirror(w['text']) for w in sorted(band, key=lambda w: (round(w['x0']), w['top'])))
        band_defs.append([min(xs), max(xs), name])
    band_defs.sort()
    centers: list[float] = [(lo + hi) / 2 for lo, hi, _ in band_defs]
    edges: list[float] = [(centers[i] + centers[i + 1]) / 2 for i in range(len(centers) - 1)]
    first_edge: float = band_defs[0][0] - 6

    def column_of(x: float) -> int:
        if x < first_edge:
            return -1                                # the label column (left of every header band)
        index: int = 0
        while index < len(edges) and x >= edges[index]:
            index += 1
        return index

    # bin upright body words into rows (cluster by top), then into columns by x
    upright.sort(key=lambda w: w['top'])
    row_groups: list[list[dict]] = []
    for word in upright:
        if row_groups and word['top'] - row_groups[-1][-1]['top'] > y_tol:
            row_groups.append([])
        elif not row_groups:
            row_groups.append([])
        row_groups[-1].append(word)

    grid: list[list[str]] = [[''] + [name for _lo, _hi, name in band_defs]]
    for row in row_groups:
        cells: list[str] = [''] * (len(band_defs) + 1)
        label_parts: list[str] = []
        for word in sorted(row, key=lambda w: w['x0']):
            # bin by the word's CENTER, not its left edge: vote counts are right-aligned, so a wide
            # number (a 4-digit "Times Cast") reaches left far enough that its x0 lands in the label
            # column and merges into the "Total" label -- which then stops matching a method row.
            column: int = column_of((word['x0'] + word['x1']) / 2)
            if column == -1:
                label_parts.append(word['text'])
            elif '%' not in word['text'] and re.fullmatch(r'-?[\d,]+', word['text']) and not cells[column + 1]:
                cells[column + 1] = word['text']       # the count (percents dropped)
        cells[0] = ' '.join(label_parts)
        grid.append(cells)
    return grid


def _cluster_1d(values: list[float], gap: float) -> list[float]:
    '''Split sorted values wherever the gap between neighbours exceeds `gap`; return group centers.'''
    centers: list[float] = []
    run: list[float] = []
    for value in sorted(values):
        if run and value - run[-1] > gap:
            centers.append(sum(run) / len(run))
            run = []
        run.append(value)
    if run:
        centers.append(sum(run) / len(run))
    return centers


# Textract price per PAGE by mode (us-west-2 list price, USD): the cheap words-only
# DetectDocumentText vs the ~10x AnalyzeDocument TABLES. Used only to estimate spend for the
# call accounting below -- cache hits cost nothing, so only real API calls are counted.
_TEXTRACT_PRICE_USD: dict[str, float] = {'text': 0.0015, 'TABLES': 0.015}

# Paid Textract calls this process, by mode ('text' | 'TABLES'). Cache hits are excluded, so this
# is the real spend; read it with textract_usage().
_textract_calls: collections.Counter = collections.Counter()


def textract_usage() -> dict:
    '''Paid Textract calls this process (cache hits excluded) and their estimated USD by mode.'''
    return {'calls': dict(_textract_calls),
            'usd': round(sum(_TEXTRACT_PRICE_USD.get(mode, 0) * n
                             for mode, n in _textract_calls.items()), 4)}


def _textract_blocks(file_path: str, page: int, features: tuple = ()) -> list[dict]:
    '''Textract Blocks for a page, cached under ./.cache/textract/ (the caller's cwd) so re-runs do not
    re-pay. Renders the page and deskews it (deskew helps Textract's cell assignment), then calls
    DetectDocumentText (features empty -- cheap, words only) or AnalyzeDocument (features, e.g.
    TABLES, ~10x). Inline PNG bytes, no S3. A cache MISS is a real (paid) call: it is counted in
    _textract_calls and logged with the running estimated spend. The Textract client takes AWS creds
    from the ambient environment (AWS_PROFILE), same as the Bedrock LMs -- keep AWS_PROFILE set to
    the intended account.'''
    import io
    import hashlib
    import json
    import boto3
    from PIL import Image
    from .. import rendering
    from ..pages import deskew
    tag: str = '+'.join(features) if features else 'text'
    key: str = hashlib.sha1(('%s\0%d\0%s' % (os.path.abspath(file_path), page, tag)).encode()).hexdigest()
    cache_dir: str = os.path.join('.cache', 'textract')     # ./.cache relative to the caller's cwd
    cache: str = os.path.join(cache_dir, '%s.json' % key)
    if os.path.exists(cache):
        return json.load(open(cache))
    image = Image.open(rendering.render_page(file_path, page, resolution=300)).convert('RGB')
    angle: float = deskew.detect_skew_pil(image)
    if abs(angle) > 0.05:
        image = image.rotate(-angle, resample=Image.BICUBIC, expand=True, fillcolor='white')
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    _textract_calls[tag] += 1                        # a real, paid call (cache miss)
    logger.info('textract %s p%d (%s) -- paid calls so far: %s ~$%.4f',
                tag, page, os.path.basename(file_path), dict(_textract_calls), textract_usage()['usd'])
    client = boto3.Session(region_name=os.environ.get('AWS_REGION_NAME', 'us-west-2')).client('textract')
    if features:
        blocks = client.analyze_document(Document={'Bytes': buffer.getvalue()}, FeatureTypes=list(features))['Blocks']
    else:
        blocks = client.detect_document_text(Document={'Bytes': buffer.getvalue()})['Blocks']
    os.makedirs(cache_dir, exist_ok=True)
    json.dump(blocks, open(cache, 'w'))
    return blocks


def _textract_words(file_path: str, page: int) -> list[dict]:
    '''Cheap-mode Textract words (DetectDocumentText), each with text and a normalized center.'''
    words: list[dict] = []
    for block in _textract_blocks(file_path, page):
        if block['BlockType'] != 'WORD':
            continue
        geometry = block['Geometry']['BoundingBox']
        words.append({'text': block.get('Text', ''),
                      'cx': geometry['Left'] + geometry['Width'] / 2,
                      'cy': geometry['Top'] + geometry['Height'] / 2})
    return words


def read_scanned_tables(file_path: str, page: int) -> list[list[list[str]]]:
    '''EVERY ruled table on a scanned page, each a grid, in top-to-bottom order (like
    source_table.page_tables, but for a scan via Textract TABLES). A scanned page routinely holds
    several contests' tables plus a header-less continuation of the previous page's contest, so the
    reader returns them all and the caller decides which belong to the target contest -- picking a
    single table here loses the header-less continuation. TABLES uses the drawn borders to segment
    cells reliably, including multi-line cells (a precinct name wrapped over several lines is ONE
    cell) and rotated headers, which our word-only reconstruction cannot group without borders.'''
    blocks = _textract_blocks(file_path, page, features=('TABLES',))
    by_id: dict[str, dict] = {b['Id']: b for b in blocks}

    def children(block: dict) -> list[dict]:
        return [by_id[i] for rel in block.get('Relationships', []) if rel['Type'] == 'CHILD'
                for i in rel['Ids']]

    def cell_text(cell: dict) -> str:
        return ' '.join(w.get('Text', '') for w in children(cell) if w['BlockType'] == 'WORD')

    found: list[tuple] = []
    for table in (b for b in blocks if b['BlockType'] == 'TABLE'):
        cells: list[dict] = [c for c in children(table) if c['BlockType'] == 'CELL']
        if not cells:
            continue
        grid: list[list[str]] = [[''] * max(c['ColumnIndex'] for c in cells)
                                 for _ in range(max(c['RowIndex'] for c in cells))]
        for cell in cells:
            grid[cell['RowIndex'] - 1][cell['ColumnIndex'] - 1] = _clean(cell_text(cell))
        found.append((table['Geometry']['BoundingBox']['Top'], grid))
    return [grid for _top, grid in sorted(found, key=lambda item: item[0])]


def read_flat_tables(file_path: str, page: int) -> list[list[list[str]]]:
    '''Flat candidate-column tables on a page, read with Textract TABLES (render the page, then
    AnalyzeDocument) -- for BOTH scanned and vector pages. Textract's table detection segments
    stacked contests into separate clean tables and reads full candidate headers even on a
    BORDERLESS dense layout (e.g. Electionware candidates-as-columns), where pdfplumber's
    text-strategy geometry returns one messy merged grid with wrapped, fragmented headers. Rendering
    a vector page costs a Textract call, but the flat path is chosen deliberately (read_strategy
    flat_tables / ruled_scan), so paying for the robust reader is the point; the free readers still
    serve the 'auto' path. This is READ MECHANICS -- the flat CONTENT handling downstream is the same.'''
    return read_scanned_tables(file_path, page)


def read_scanned_grid(file_path: str, page: int, row_gap: float = 0.006, col_gap: float = 0.02,
                      data_left: float = 0.14, half: float = 0.45) -> list[list[str]]:
    '''Read a SCANNED SOVC page into a grid from cheap-mode Textract words -- no vendor table cells.

    Cluster words into rows by their y-center; take column x-centers from the counts on real DATA
    rows only (>= 4 integer tokens in the data region), so banner rows and stray precinct numbers do
    not invent columns. A method row's counts snap to the nearest column; a precinct-label row (< 4
    counts, its name possibly wrapped across lines) keeps the left-half text as its label (the layout
    duplicates the label across a left statistics block and a right votes block -- we take the left
    copy). The header band below the contest title is binned into columns so candidate names land
    under their column for the interpreter. Thresholds are normalized (0-1) page fractions; they fit
    the MI SOVC scan layout and may need widening for other vendors.'''
    words: list[dict] = _textract_words(file_path, page)
    if not words:
        return []
    words.sort(key=lambda w: w['cy'])
    rows: list[list[dict]] = []
    for word in words:
        if rows and word['cy'] - rows[-1][-1]['cy'] > row_gap:
            rows.append([])
        elif not rows:
            rows.append([])
        rows[-1].append(word)

    def data_counts(row: list[dict]) -> list[dict]:
        return [w for w in row if re.fullmatch(r'-?[\d,]+', w['text']) and data_left < w['cx'] < 0.95]

    data_rows: list[list[dict]] = [row for row in rows if len(data_counts(row)) >= 4]
    if not data_rows:
        return []
    centers: list[float] = _cluster_1d([w['cx'] for row in data_rows for w in data_counts(row)], col_gap)
    label_bound: float = centers[0] - 0.04
    first_data_y: float = min(min(w['cy'] for w in row) for row in data_rows)
    nearest = lambda cx: min(range(len(centers)), key=lambda i: abs(centers[i] - cx))

    header: list[str] = [''] * (len(centers) + 1)
    for row in rows:
        for word in row:
            if 0.12 < word['cy'] < first_data_y and word['cx'] >= label_bound and '%' not in word['text']:
                header[nearest(word['cx']) + 1] = (header[nearest(word['cx']) + 1] + ' ' + word['text']).strip()

    grid: list[list[str]] = [header]
    for row in sorted(rows, key=lambda r: min(w['cy'] for w in r)):
        counts = data_counts(row)
        cells: list[str] = [''] * (len(centers) + 1)
        if len(counts) >= 4:                             # method row: label at left, counts in columns
            cells[0] = ' '.join(w['text'] for w in sorted(row, key=lambda w: w['cx']) if w['cx'] < label_bound)
            for word in counts:
                cells[nearest(word['cx']) + 1] = word['text']
        else:                                            # precinct-label / banner row: left-half text
            cells[0] = ' '.join(w['text'] for w in sorted(row, key=lambda w: w['cx']) if w['cx'] < half)
        grid.append(cells)

    # Rejoin a precinct name that wrapped across lines so the interpreter's first_data_row cannot
    # split it: MI names wrap as "<place>," + "Precinct N", so a label-only row whose nearest
    # preceding label-only row ends in a comma is that row's continuation (blank lines between are
    # skipped). Banners and section headers do not end in a comma, so they are untouched.
    previous: list[str] | None = None
    for cell_row in grid[1:]:
        if not any(cell_row):
            continue                                     # blank row: keep the pending label
        is_label: bool = bool(cell_row[0]) and not any(cell_row[1:])
        if is_label and previous is not None and previous[0].rstrip().endswith(','):
            previous[0] = (previous[0] + ' ' + cell_row[0]).strip()
            cell_row[0] = ''
        else:
            previous = cell_row if is_label else None
    return grid


def read_page_grid(file_path: str, page: int) -> list[list[str]]:
    '''One page's grid, choosing the reader by what the page offers: ruled Hart SOVC
    (source_table) -> rotated-header text-aligned SOVC (read_rotated_grid) -> a borderless
    text-aligned vector page (read_text_grid) -> a scanned page with no text layer at all
    (read_scanned_grid via Textract). The text-alignment reader fills the columns path for a
    borderless VECTOR page (e.g. Electionware laid out candidates-as-columns) that has no ruled
    lines for source_table and is not rotated; the Textract path only fires when the page has no
    extractable words, so a vector document never pays for it.'''
    grid: list[list[str]] = source_table.page_table(file_path, page) or read_rotated_grid(file_path, page)
    if grid:
        return grid
    pdf: pdfplumber.PDF = _open_pdf(file_path)
    if 1 <= page <= len(pdf.pages):
        if pdf.pages[page - 1].extract_words():
            return read_text_grid(file_path, page)          # borderless text-aligned vector
        return read_scanned_grid(file_path, page)           # scanned, no text layer
    return []


def _has_text_layer(file_path: str, page: int) -> bool:
    '''True when the page has an extractable text layer (a vector PDF page); False for a scan
    (no words) -- the same signal read_page_grid uses to gate the Textract path.'''
    try:
        pdf: pdfplumber.PDF = _open_pdf(file_path)
    except Exception:
        return True                                  # non-PDF (spreadsheet): treat as vector/text
    if 1 <= page <= len(pdf.pages):
        return bool(pdf.pages[page - 1].extract_words())
    return True


def detect_dispatch(file_path: str, page: int) -> dict:
    '''Choose how to read a contest from ONE sample page, via oe2d.pages (the image VLM) plus a
    deterministic text-layer check -- so dispatch comes from the page image, not a hand-set gold
    field. Returns {orientation, read_strategy, scanned, ruled_table}.

    orientation is the VLM's candidate_orientation (columns/rows). A page with no text layer is a
    scan. read_strategy is a PROPOSAL: a scanned page the VLM calls ruled proposes 'ruled_scan'
    (Textract TABLES), everything else 'auto' (the reader self-detects). ruled_table alone cannot
    settle the scanned read -- a ruled scan whose rules are faint/broken (Gogebic) makes TABLES
    mis-segment even though huron's clean rules do not -- so the caller must CONFIRM a proposed
    ruled_scan read with a checksum and fall back to 'auto' when it does not reconcile.'''
    from .. import pages
    props: dict = pages.analyze_page(file_path, page)
    scanned: bool = not _has_text_layer(file_path, page)
    ruled: bool = bool(props.get('ruled_table'))
    return {'orientation': props['candidate_orientation'],
            'read_strategy': 'ruled_scan' if (scanned and ruled) else 'auto',
            'scanned': scanned, 'ruled_table': ruled}


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
    # A precinct label at the very bottom of the page whose vote rows continue onto the NEXT page
    # leaves label_parts set with no methods: emit it as a partial block (empty methods) so the
    # cross-page stitch can join it to that next page's leading (label-less) vote rows.
    if label_parts and not methods:
        blocks.append({'label': ' '.join(label_parts), 'methods': {}})
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


class VoteExtractor(dspy.Module):
    '''The full vote-extraction program: read -> interpret (LLM) -> stitch -> canonical rows.

    Mirrors oe2d.pages.PageAnalyzer / oe2d.contests.ContestLocator: ONE composite dspy.Module IS
    the program, so Cmpnd sees a single traced read->interpret->stitch flow and GEPA can evolve
    every prompt in it. The two NAMED inner predictors are what GEPA optimizes -- their
    signature-docstring instructions carry all the how-to-decide guidance (candidate matching,
    write-in vs write-in-total, over/under-vote canonicalization); the pydantic Field descriptions
    stay minimal and structural (GEPA can't reach them). Everything else -- the reader dispatch,
    walk_page, _precinct_groups, cross-page stitch, consensus, _assign_methods /
    _consolidate_write_in, the all-zero drop -- is deterministic Python that moves the digits, traced
    but OUTSIDE the GEPA objective (exactly like PageAnalyzer's skew and ContestLocator's tools).

    forward(file_path, pages, office, candidate_context, county, district, orientation,
    read_strategy) dispatches on READ MECHANICS (read_strategy) and CONTENT structure (orientation)
    -- kept orthogonal -- and returns dspy.Prediction(rows=<canonical CSV rows>, votes=<stitched
    mapping>). The LLM never reads or returns a number.
    '''
    def __init__(self) -> None:
        super().__init__()
        # columns (contest-major): precincts down rows, candidates across columns.
        self.interpret_columns: dspy.Module = dspy.Predict(signatures.InterpretResultsPage)
        # rows (precinct-major): one precinct per page, candidates down rows, methods across columns.
        self.interpret_rows: dspy.Module = dspy.Predict(signatures.InterpretPrecinctPage)

    def _columns_schema(self, office: str, candidate_context: str,
                        rows: list[list[str]]) -> signatures.PageSchema:
        '''Interpret one contest-major page's grid into a PageSchema (the LLM step; no numbers).'''
        return self.interpret_columns(office=office, candidate_context=candidate_context,
                                      grid=grid_to_text(rows)).page_schema

    def forward(self, file_path: str, pages: list[int], office: str, candidate_context: str,
                county: str = '', district: str = '', orientation: str | None = None,
                read_strategy: ReadStrategy | None = None) -> dspy.Prediction:
        '''Read -> interpret -> stitch -> canonical rows for one contest. orientation is CONTENT
        structure ('rows' -> precinct-major, 'columns' -> contest-major); read_strategy is READ
        MECHANICS ('ruled_scan' reads Textract TABLES across the pages, 'auto' self-detects). Either
        left None is DETECTED from a sample page via oe2d.pages (detect_dispatch), so dispatch comes
        from the image, not a caller-set field; pass a value to override. Returns rows + the raw
        stitched votes mapping.'''
        if orientation is None or read_strategy is None:
            detected: dict = detect_dispatch(file_path, pages[0])
            orientation = orientation or detected['orientation']
            read_strategy = read_strategy or detected['read_strategy']
        votes: dict = self._read_votes(file_path, pages, office, candidate_context,
                                       orientation, read_strategy)
        rows: list[dict] = votes_to_rows(votes, county, office, district)
        return dspy.Prediction(rows=rows, votes=votes)

    def _read_votes(self, file_path: str, pages: list[int], office: str, candidate_context: str,
                    orientation: str, read_strategy: ReadStrategy) -> dict:
        '''Run the chosen read, CONFIRMING a ruled_scan with the printed-total checksum and falling
        back to the auto read when it does not reconcile. A ruled scan whose rules are faint/broken
        (Gogebic) makes Textract TABLES mis-segment -- and a ruled scan that is really method-sub-row
        content, not flat, doubles every column -- so the flat read's per-candidate sums will not
        equal the county-total row; that mismatch drops us to the cheap auto read (which the reader
        self-detects for a scan). huron's clean flat table reconciles and stays on TABLES.'''
        if read_strategy in ('ruled_scan', 'flat_tables'):
            votes, totals = self._extract_scanned_tables(file_path, pages, office, candidate_context)
            if _reconciles(votes, totals):
                return votes
            logger.info('flat-tables read did not reconcile with printed county totals '
                        '(%d candidate column(s)); falling back to auto/cheap read', len(totals))
        if orientation == 'rows':
            return self._extract_precinct_contest(file_path, pages, office, candidate_context)
        return self._extract_contest(file_path, pages, office, candidate_context)

    def _extract_contest(self, file_path: str, pages: list[int], office: str,
                         candidate_context: str) -> dict:
        '''Read the contest's pages and stitch them into votes[(precinct, candidate, party)][bucket].

        Reads each page, interprets it (LLM), walks it into ordered precinct blocks, partitions the
        pages into precinct-groups, then within a group concatenates candidate columns across the
        candidate-group pages by precinct position and across groups appends the precinct lists. The
        interpreter never touches a number; this moves them.
        '''
        page_schemas: list[tuple] = []
        for page in pages:
            rows: list[list[str]] = read_page_grid(file_path, page)
            schema: signatures.PageSchema = self._columns_schema(office, candidate_context, rows)
            page_schemas.append((schema, rows))

        # A first walk with each page's own skip labels, to learn the document's real precinct labels.
        first_blocks: list[list[dict]] = [walk_page(rows, schema) for schema, rows in page_schemas]
        precinct_labels: set[str] = {block['label'] for blocks in first_blocks
                                     for block in blocks if block['label']}

        # Consensus skip labels: a label is a total/header only if MULTIPLE pages call it one AND it
        # is not a fragment of any real precinct label. This drops a single page's mistake (a precinct
        # name in skip_labels collapses that group) and a common wrap fragment ("Precinct 1") that
        # several pages mis-skip -- while keeping a genuine total ("Barry County Michigan"), which
        # never appears inside a precinct name.
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
            logger.info('page: %d columns, %d precinct blocks',
                        len(schema.columns), len(pages_schema_blocks[-1][1]))

        # Cross-page precinct stitching (vertical continuation): a precinct whose rows straddle a page
        # break leaves its label -- and any early method rows that still fit -- as the LAST block of
        # page N, and the remaining method rows as a label-less FIRST block of page N+1. Merge the
        # two: the tail's label and methods fold into the next page's leading block. Guard by
        # requiring the two blocks' method buckets to be DISJOINT -- a true straddle splits the four
        # methods across the break (Election Day here, the rest there), whereas a complete last
        # precinct followed by a separately label-less one would overlap. Same-contest continuation
        # means identical columns, so reading the tail's rows under the next page's schema is safe.
        # No-op for documents (Barry, Oscoda) whose precincts never straddle. Then drop any partial
        # with no continuation.
        for index in range(len(pages_schema_blocks) - 1):
            current: list[dict] = pages_schema_blocks[index][1]
            following: list[dict] = pages_schema_blocks[index + 1][1]
            if not (current and following):
                continue
            tail: dict = current[-1]
            head: dict = following[0]
            if tail['label'] and head['label'] is None and head['methods'] \
                    and not (tail['methods'].keys() & head['methods'].keys()):
                head['label'] = tail['label']
                for bucket, row in tail['methods'].items():
                    head['methods'].setdefault(bucket, row)
                current.pop()
        for _schema, blocks in pages_schema_blocks:
            blocks[:] = [block for block in blocks if block['methods']]

        votes: dict = {}
        # write-in columns are collected per (precinct, method), keeping the interpreter's total-vs-
        # component split, and consolidated once at the end: a real total wins, else components sum.
        write_ins: dict = collections.defaultdict(
            lambda: collections.defaultdict(lambda: {'total': [], 'component': []}))
        for group in _precinct_groups(pages_schema_blocks):
            # Precinct labels by consensus across the group's candidate-group pages: a page may drop
            # the first precinct's label to None (a sibling page carries it) or read a wrapped name as
            # only a fragment ("Precinct 1" for "Ironwood Charter Township, Precinct 1"); take the
            # MOST COMPLETE (longest) reading at each position, so the fullest page wins.
            span: int = max(len(blocks) for _schema, blocks in group)
            labels: list = []
            for index in range(span):
                seen = [blocks[index]['label'] for _schema, blocks in group
                        if index < len(blocks) and blocks[index]['label']]
                labels.append(max(seen, key=len) if seen else None)
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
                            if column.write_in:                     # gather; consolidated below
                                part = 'total' if column.write_in_total else 'component'
                                write_ins[precinct][store][part].append(value)
                            else:
                                votes.setdefault((precinct, candidate, party), {})[store] = value
        for precinct, stores in write_ins.items():
            votes[(precinct, WRITE_IN_LABEL, '')] = {
                store: _consolidate_write_in(parts['total'], parts['component'])
                for store, parts in stores.items()}
        return votes

    def _extract_precinct_contest(self, file_path: str, pages: list[int], office: str,
                                  candidate_context: str) -> dict:
        '''Extract a candidates-as-rows contest whose precincts are one-per-page (precinct in the page
        header, methods across columns). The document's pages are structurally identical, so interpret
        ONE sample page and apply that schema to every page -- one LLM call per document, then
        deterministic exact-label extraction. Candidate rows are found by their verbatim row-label
        (identical across pages); the precinct name is read from the learned header cell.'''
        sample: list[list[str]] = read_text_grid(file_path, pages[0])
        schema: signatures.PrecinctPageSchema = self.interpret_rows(
            office=office, candidate_context=candidate_context, grid=grid_to_text(sample)).precinct_schema
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
        write_ins: dict = collections.defaultdict(
            lambda: collections.defaultdict(lambda: {'total': [], 'component': []}))
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
                if role.write_in:                                 # gather; consolidated below
                    part = 'total' if role.write_in_total else 'component'
                    for bucket, value in record.items():
                        write_ins[precinct][bucket][part].append(value)
                else:
                    candidate: str = re.sub(r':+$', '', role.candidate).strip()   # drop colon artifact
                    votes[(precinct, candidate, role.party)] = record
        for precinct, stores in write_ins.items():
            votes[(precinct, WRITE_IN_LABEL, '')] = {
                store: _consolidate_write_in(parts['total'], parts['component'])
                for store, parts in stores.items()}
        return votes

    def _extract_scanned_tables(self, file_path: str, pages: list[int], office: str,
                                candidate_context: str) -> tuple[dict, dict]:
        '''Extract a contest read from a ruled scan as tables (read_scanned_tables), scoping to the
        contest by column structure and reading CONTENT the interpreter reports.

        A scanned page holds several contests' tables plus a header-less continuation of the previous
        page's contest, so we read EVERY table and scope by column structure: learn the candidate
        columns ONCE from the table whose header matches the expected candidates (the anchor), then
        take the anchor and every table sharing its column count -- the anchor and its header-less
        continuations. The interpreter is used only to map header columns to candidates; this reads a
        FLAT table (one row per precinct, one total per candidate, no vote-method sub-rows) directly
        from those columns, so it is independent of method_labels and never touches the sub-row walker.

        Returns (votes, totals): votes as elsewhere, and totals mapping each non-write-in candidate
        column to the printed COUNTY-TOTAL row's value for that column -- the checksum target _read_votes
        reconciles the ruled_scan read against (Sigma precincts == printed total per candidate).
        '''
        tables: list[list[list[str]]] = [grid for page in pages
                                         for grid in read_flat_tables(file_path, page)]
        if not tables:
            return {}, {}
        wanted: set = {_norm(token) for line in candidate_context.splitlines()
                       for token in re.split(r'\s*\(', line)[0].split() if len(token) > 3}
        header_match = lambda grid: sum(1 for token in wanted if grid and token in _norm(' '.join(grid[0])))
        anchor: list[list[str]] = max(tables, key=header_match)
        schema: signatures.PageSchema = self._columns_schema(office, candidate_context, anchor)
        column_count: int = len(anchor[0])
        label_column: int = schema.label_column
        candidates = [column for column in schema.columns if column.role == 'candidate']

        def has_data(row: list[str]) -> bool:
            return any(column.index < len(row) and _parse_number(row[column.index]) is not None
                       for column in candidates)

        def label_of(row: list[str]) -> str:
            return _clean(row[label_column]) if label_column < len(row) else ''

        # Build the precinct list across the contest's tables in page order, stitching a precinct
        # whose row STRADDLES a page: its data sits at the bottom of one page under a (truncated)
        # label, and the label continues on a label-only row at the top of the next table. A
        # label-only row that appears before any precinct of a continuation table is that
        # continuation, so append it to the previous precinct. Precincts run down the rows, so nothing
        # needs positional alignment.
        entries: list[list] = []                         # [label, data_row]
        for grid in tables:                              # anchor + its column-count-matching continuations
            if not grid or len(grid[0]) != column_count:
                continue
            started: bool = False
            for row in grid:
                if has_data(row):
                    entries.append([label_of(row), row])
                    started = True
                elif label_of(row) and not started and entries:
                    entries[-1][0] = (entries[-1][0] + ' ' + label_of(row)).strip()

        votes: dict = {}
        totals: dict = {}                                # (candidate, party) -> printed county total
        write_ins: dict = collections.defaultdict(lambda: {'total': [], 'component': []})
        for precinct, row in entries:
            # a grand-total row is a CHECKSUM, not a precinct; the interpreter flags it in skip_labels
            # when the anchor table shows it, but the total can sit on a header-less continuation the
            # anchor never saw, so also treat a bare "Total" label as one. Capture its non-write-in
            # candidate values (the largest wins if several total-like rows appear) for reconciliation.
            is_total: bool = (not precinct or precinct in schema.skip_labels
                              or _norm(precinct) in ('total', 'totals'))
            for column in candidates:
                value = _parse_number(row[column.index]) if column.index < len(row) else None
                if value is None:
                    continue
                candidate, party = _split_party(column.candidate, column.party)
                if is_total:
                    if not column.write_in:
                        totals[(candidate, party)] = max(value, totals.get((candidate, party), 0))
                elif column.write_in:
                    write_ins[precinct]['total' if column.write_in_total else 'component'].append(value)
                else:
                    votes[(precinct, candidate, party)] = {'votes': value}
        for precinct, parts in write_ins.items():
            votes[(precinct, WRITE_IN_LABEL, '')] = {
                'votes': _consolidate_write_in(parts['total'], parts['component'])}
        return votes, totals


def build_extractor() -> VoteExtractor:
    '''Construct the composite vote extractor. A trained artifact, when present, fully governs (its
    saved prompts AND lm win -- see the LM-artifact-authority note); otherwise bind the stock
    inference LM (temperature 0). Turns on cmpnd tracing here, once, so every read/interpret/stitch
    flow reports its LLM calls under one program. Replaces the old per-call build_interpreter /
    build_precinct_interpreter (the two interpreters now live on the shared module).'''
    _instrument()
    extractor: VoteExtractor = VoteExtractor()
    if os.path.exists(OPTIMIZED_MODEL_PATH):
        extractor.load(OPTIMIZED_MODEL_PATH)
    else:
        extractor.set_lm(dspy.LM(LM_CLAUDE_SONNET45, temperature=0.0, max_tokens=4096))
    return extractor


def extract(file_path: str, pages: list[int], office: str, candidate_context: str,
            county: str = '', district: str = '', orientation: str | None = None,
            read_strategy: ReadStrategy | None = None,
            extractor: VoteExtractor | None = None) -> dict:
    '''Convenience wrapper: build (or reuse) a VoteExtractor and return its stitched votes mapping
    (not the canonical rows). orientation/read_strategy left None are detected from the image (see
    forward). Prefer the module directly -- extractor(file_path=..., ...) -- when you want the traced
    dspy.Prediction; this keeps the historic function-style entry point for scripts.'''
    extractor = extractor or build_extractor()
    return extractor(file_path=file_path, pages=pages, office=office,
                     candidate_context=candidate_context, county=county, district=district,
                     orientation=orientation, read_strategy=read_strategy).votes


def votes_to_rows(votes: dict, county: str, office: str, district: str = '') -> list[dict]:
    '''Canonical precinct rows from a stitched votes mapping.

    Drop a precinct whose every candidate total is zero: in these MI reports a zero-vote precinct is
    an out-of-county split fragment the county lists as a placeholder but does not own ("Chester
    Township (Eaton OOC)", "Delaware Township (Sanilac County)" -- confirmed a Sanilac township), and
    the human-authored CSVs exclude them. This is a NUMERIC data-integrity rule (no votes -> not a
    result row); it reads no text, so it is not a language/terminology decision.'''
    live: set = {precinct for (precinct, _candidate, _party), buckets in votes.items()
                 if (buckets.get('votes') or 0)}
    rows: list[dict] = []
    for (precinct, candidate, party), buckets in votes.items():
        if precinct not in live:
            continue
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
    parser.add_argument('--orientation', default=None, choices=('columns', 'rows'),
                        help='CONTENT structure override; default: detected from the page via oe2d.pages')
    parser.add_argument('--read-strategy', default=None, choices=('auto', 'ruled_scan'),
                        help='READ MECHANICS override; default: detected from the page (checksum-confirmed)')
    parser.add_argument('-v', '--verbose', action='store_true')
    args: argparse.Namespace = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format='%(message)s')
        logging.getLogger('oe2d').setLevel(logging.INFO)

    extractor: VoteExtractor = build_extractor()             # instruments cmpnd once
    prediction = extractor(file_path=args.path, pages=_parse_pages(args.pages), office=args.office,
                           candidate_context=resolve_context(args.context), county=args.county,
                           district=args.district, orientation=args.orientation,
                           read_strategy=args.read_strategy)
    writer = csv.DictWriter(sys.stdout, CANON_COLUMNS)
    writer.writeheader()
    writer.writerows(prediction.rows)


if __name__ == '__main__':
    main()
