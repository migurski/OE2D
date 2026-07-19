'''Categorize an election-results source file for routing to an extractor.

Usage: oe2d-categorize-source path/to/file

Prints a JSON dict describing the source: its container format, table
orientation, geographic grain, and layout properties. A deterministic layer
sniffs the container and page count; a DSPy RLM then inspects the file with tools
(including a vision inspector) to fill in orientation, grain, and the layout
properties.
Requires DSPy, Bedrock credentials, Deno, and LibreOffice — missing pieces fail
loudly rather than degrading to a partial result.
'''
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import typing
import zipfile

import dspy
import pdfplumber
import pydantic

import source_table


# Allowed label vocabularies — these Literal types define the routing
# taxonomy. The tuples are derived from them so runtime membership checks and
# the pydantic/DSPy field types can never drift apart.
Container = typing.Literal[
    'vector_pdf', 'scanned_pdf', 'xlsx', 'xls_binary', 'xls_xml',
    'csv', 'txt', 'zip', 'unknown',
]
Orientation = typing.Literal['candidate_columns', 'candidate_rows', 'unknown']
Grain = typing.Literal['precinct', 'district', 'county', 'unknown']

CONTAINERS: tuple[str, ...] = typing.get_args(Container)
ORIENTATIONS: tuple[str, ...] = typing.get_args(Orientation)
GRAINS: tuple[str, ...] = typing.get_args(Grain)

# Containers whose first tabular page source_table can read directly.
_TABULAR_CONTAINERS: tuple[str, ...] = ('vector_pdf', 'xlsx', 'xls_binary', 'xls_xml')
_PAGED_CONTAINERS: tuple[str, ...] = ('vector_pdf', 'scanned_pdf', 'xlsx', 'xls_binary', 'xls_xml')
_EXT_CONTAINERS: dict[str, Container] = {'.xlsx': 'xlsx', '.csv': 'csv', '.txt': 'txt', '.zip': 'zip'}


# Layout properties — top-level boolean facts about how a source's tables are
# laid out. OCR-needed is not one; it is implied by the scanned_pdf container.
LAYOUT_PROPERTY_DESCRIPTIONS: dict[str, str] = {
    'has_rotated_headers': 'Column headers are rotated / vertical text',
    'has_stacked_contests': 'Two or more contests stacked vertically on one page or sheet',
    'has_side_by_side': 'Two or more contests placed side by side',
    'has_multi_sheet_stitch': 'Data split across sheets or pages that must be stitched together',
}
LAYOUT_PROPERTIES: tuple[str, ...] = tuple(LAYOUT_PROPERTY_DESCRIPTIONS)


class SourceCategory(pydantic.BaseModel):
    '''Categorization of a single source file for extractor routing.'''
    path: str
    file_name: str
    container: Container
    page_count: int
    orientation: Orientation
    grain: Grain
    has_rotated_headers: bool = pydantic.Field(
        False, description=LAYOUT_PROPERTY_DESCRIPTIONS['has_rotated_headers'])
    has_stacked_contests: bool = pydantic.Field(
        False, description=LAYOUT_PROPERTY_DESCRIPTIONS['has_stacked_contests'])
    has_side_by_side: bool = pydantic.Field(
        False, description=LAYOUT_PROPERTY_DESCRIPTIONS['has_side_by_side'])
    has_multi_sheet_stitch: bool = pydantic.Field(
        False, description=LAYOUT_PROPERTY_DESCRIPTIONS['has_multi_sheet_stitch'])


def detect_container(path: str) -> Container:
    '''Sniff the container format from extension and file content.'''
    ext: str = os.path.splitext(path)[1].lower()
    if ext == '.pdf':
        return _detect_pdf_kind(path)
    if ext == '.xls':
        return _detect_xls_kind(path)
    return _EXT_CONTAINERS.get(ext, 'unknown')


def _detect_pdf_kind(path: str) -> Container:
    '''Distinguish a vector PDF (extractable text) from a scanned bitmap.'''
    pdf: pdfplumber.PDF = pdfplumber.open(path)
    try:
        char_total: int = sum(len(page.chars) for page in pdf.pages[:5])
    finally:
        pdf.close()
    return 'vector_pdf' if char_total > 20 else 'scanned_pdf'


def _detect_xls_kind(path: str) -> Container:
    '''Distinguish a binary BIFF .xls from an XML SpreadsheetML .xls.'''
    with open(path, 'rb') as file:
        head: bytes = file.read(20)
    if head.lstrip(b'\xef\xbb\xbf').startswith(b'<?xml'):
        return 'xls_xml'
    return 'xls_binary'


def count_pages(path: str, container: str) -> int:
    '''Count pages (PDF), sheets (Excel), or members (zip).'''
    if container in _PAGED_CONTAINERS:
        try:
            return source_table.page_count(path)
        except Exception:
            return 0
    if container == 'zip':
        with zipfile.ZipFile(path) as archive:
            return len(archive.namelist())
    return 1


def grain_from_name(file_name: str) -> Grain:
    '''Guess geographic grain from cues in the file name.'''
    low: str = file_name.lower()
    if 'precinct' in low:
        return 'precinct'
    if 'district' in low:
        return 'district'
    if 'county-level' in low or 'county level' in low:
        return 'county'
    return 'unknown'


def content_preview(path: str, container: str, rows: int = 8, cols: int = 12) -> str:
    '''Build a short text preview of the first tabular page.

    Reads structured rows via source_table for Excel and vector PDFs, or the
    raw first lines for csv/txt. Returns '' for containers with no readable
    text (scanned PDFs, zips).
    '''
    if container in ('csv', 'txt'):
        return _raw_line_preview(path, rows)
    if container in _TABULAR_CONTAINERS:
        try:
            table: list[list[str]] | None = source_table.page_table(path, 1)
        except Exception:
            table = None
        if not table:
            return ''
        lines: list[str] = [
            ' | '.join(cell[:40] for cell in row[:cols]) for row in table[:rows]
        ]
        return '\n'.join(lines)
    return ''


def _raw_line_preview(path: str, rows: int) -> str:
    '''Read the first few raw lines of a text file for preview.'''
    lines: list[str] = []
    try:
        with open(path, encoding='utf-8', errors='replace') as file:
            for _, line in zip(range(rows), file):
                lines.append(line.rstrip('\n')[:200])
    except OSError:
        return ''
    return '\n'.join(lines)


# Bedrock's Llama-4 Maverick (multimodal) drives both the RLM code-writing and
# the vision inspector. Hardcoded — no per-run model override needed.
MAVERICK_LM = 'bedrock/us.meta.llama4-maverick-17b-instruct-v1:0'


class SourceCategorizer(dspy.Signature):
    '''Categorize an election-results source file for extractor routing.

    Look at the file with the tools before answering. Call them with just the
    file path and a page/sheet number — do NOT pass the container to them; pass
    member= (keyword) only to read a file inside a zip.
    - page_count, page_table, page_words read text; for spreadsheets the page
      argument is the sheet number. zip_members lists archive contents.
    - inspect_page renders a page/sheet and returns what a vision model sees. It
      is REQUIRED for scanned_pdf sources (no extractable text) and useful to
      confirm rotated headers or side-by-side/stacked contests.
    Many spreadsheets lead with a table-of-contents sheet, so look past it at an
    actual contest sheet.

    orientation: 'candidate_columns' when each candidate is a column and
    precincts are rows; 'candidate_rows' when each candidate is a row.
    grain: geographic grain — 'precinct', 'district', or 'county'.
    The has_* layout properties are boolean facts about the table layout; set
    each true or false. OCR-needed is not one; it is implied by scanned_pdf.
    '''
    file_path: str = dspy.InputField(desc='Path to the source file for the tools')
    container: str = dspy.InputField(desc='Detected container format')
    page_count: int = dspy.InputField(desc='Pages (PDF) or sheets (spreadsheet)')
    orientation: Orientation = dspy.OutputField()
    grain: Grain = dspy.OutputField()
    has_rotated_headers: bool = dspy.OutputField(
        desc=LAYOUT_PROPERTY_DESCRIPTIONS['has_rotated_headers'])
    has_stacked_contests: bool = dspy.OutputField(
        desc=LAYOUT_PROPERTY_DESCRIPTIONS['has_stacked_contests'])
    has_side_by_side: bool = dspy.OutputField(
        desc=LAYOUT_PROPERTY_DESCRIPTIONS['has_side_by_side'])
    has_multi_sheet_stitch: bool = dspy.OutputField(
        desc=LAYOUT_PROPERTY_DESCRIPTIONS['has_multi_sheet_stitch'])


def _instrument() -> None:
    '''Turn on cmpnd tracing when a key is configured; otherwise do nothing.'''
    key: str | None = os.environ.get('CMPND_API_KEY')
    if not key:
        return
    try:
        import cmpnd
        cmpnd.configure(
            api_key=key,
            endpoint=os.environ.get('CMPND_ENDPOINT'),
            project_tags=['oe2d-categorize'],
        )
        cmpnd.auto_instrument()
    except Exception:
        pass


def run_rlm(signals: dict, verbose: bool = False) -> dict:
    '''Categorize with a DSPy RLM that inspects the file through tools.

    The RLM writes Python in a sandbox and calls host-side tools (page_count,
    page_table, page_words, zip_members, inspect_page). inspect_page renders a
    page/sheet and runs a vision model on it, so scanned PDFs and visually
    complex layouts are read from the image rather than guessed. Raises on any
    missing runtime piece (Bedrock credentials, Deno, LibreOffice) rather than
    hiding it behind a partial result.
    '''
    from . import tools

    _instrument()
    # One ambient LM drives the RLM and, through the shared dspy.settings, the
    # vision inspector too.
    dspy.configure(lm=dspy.LM(MAVERICK_LM))
    categorizer = dspy.RLM(
        SourceCategorizer,
        tools=[tools.page_count, tools.page_table, tools.page_words,
               tools.zip_members, tools.inspect_page],
        verbose=verbose,
    )
    prediction = categorizer(
        file_path=signals['path'],
        container=signals['container'],
        page_count=signals['page_count'],
    )
    return {
        'orientation': prediction.orientation,
        'grain': prediction.grain,
        'has_rotated_headers': prediction.has_rotated_headers,
        'has_stacked_contests': prediction.has_stacked_contests,
        'has_side_by_side': prediction.has_side_by_side,
        'has_multi_sheet_stitch': prediction.has_multi_sheet_stitch,
    }


def categorize(path: str, verbose: bool = False) -> dict:
    '''Categorize a source file, returning a plain JSON-serializable dict.'''
    file_name: str = os.path.basename(path)
    container: str = detect_container(path)
    pages: int = count_pages(path, container)
    name_grain: str = grain_from_name(file_name)

    signals: dict = {
        'path': path,
        'file_name': file_name,
        'container': container,
        'page_count': pages,
    }

    llm: dict = run_rlm(signals, verbose=verbose)
    grain: Grain = llm['grain'] if llm['grain'] != 'unknown' else name_grain

    category = SourceCategory(
        path=path,
        file_name=file_name,
        container=container,
        page_count=pages,
        orientation=llm['orientation'],
        grain=grain,
        has_rotated_headers=llm['has_rotated_headers'],
        has_stacked_contests=llm['has_stacked_contests'],
        has_side_by_side=llm['has_side_by_side'],
        has_multi_sheet_stitch=llm['has_multi_sheet_stitch'],
    )
    return category.model_dump()


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='Categorize an election-results source file.',
    )
    parser.add_argument('path', help='Path to the source file')
    parser.add_argument('-q', '--quiet', action='store_true',
                        help='suppress the RLM execution trace')
    args: argparse.Namespace = parser.parse_args()

    verbose: bool = not args.quiet
    if verbose:
        # RLM logs its REPL steps at INFO; send them to stderr so stdout stays
        # pure JSON and the trace is watchable alongside it.
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format='%(message)s')
        logging.getLogger('dspy').setLevel(logging.INFO)

    print(json.dumps(categorize(args.path, verbose=verbose), indent=2))


if __name__ == '__main__':
    main()
