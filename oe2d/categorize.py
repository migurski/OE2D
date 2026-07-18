'''Categorize an election-results source file for routing to an extractor.

Usage: oe2d-categorize-source path/to/file

Prints a JSON dict describing the source: its container format, table
orientation, geographic grain, and layout quirks. A deterministic layer
sniffs the container and reads a content preview; a DSPy program fills in
the semantic fields (orientation, grain, quirks) from the file name and
preview. The DSPy step is skipped when no LM is configured, so the CLI and
its container detection stay testable without model credentials.
'''
from __future__ import annotations

import argparse
import json
import os
import sys
import typing
import zipfile

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
# OCR-needed is implied by the scanned_pdf container, so it is not a quirk.
Quirk = typing.Literal[
    'rotated_headers', 'stacked_contests', 'side_by_side',
    'multi_sheet_stitch',
]

CONTAINERS: tuple[str, ...] = typing.get_args(Container)
ORIENTATIONS: tuple[str, ...] = typing.get_args(Orientation)
GRAINS: tuple[str, ...] = typing.get_args(Grain)
QUIRKS: tuple[str, ...] = typing.get_args(Quirk)

# Containers whose first tabular page source_table can read directly.
_TABULAR_CONTAINERS: tuple[str, ...] = ('vector_pdf', 'xlsx', 'xls_binary', 'xls_xml')
_PAGED_CONTAINERS: tuple[str, ...] = ('vector_pdf', 'scanned_pdf', 'xlsx', 'xls_binary', 'xls_xml')
_EXT_CONTAINERS: dict[str, Container] = {'.xlsx': 'xlsx', '.csv': 'csv', '.txt': 'txt', '.zip': 'zip'}


class SourceCategory(pydantic.BaseModel):
    '''Categorization of a single source file for extractor routing.'''
    path: str
    file_name: str
    container: Container
    page_count: int
    orientation: Orientation
    grain: Grain
    quirks: list[Quirk]
    llm_used: bool


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


def _llm_enabled() -> bool:
    '''Whether to attempt the DSPy categorization step.'''
    if os.environ.get('OE2D_NO_LM'):
        return False
    if os.environ.get('OE2D_LM'):
        return True
    return bool(os.environ.get('AWS_PROFILE') or os.environ.get('AWS_ACCESS_KEY_ID'))


def run_llm(signals: dict) -> dict | None:
    '''Run the DSPy categorizer over deterministic signals.

    Returns a dict with orientation, grain, and quirks, or None if DSPy or a
    language model is unavailable.
    '''
    try:
        import dspy
    except Exception:
        return None

    class SourceCategorizer(dspy.Signature):
        '''Categorize an election-results source page for extractor routing.

        orientation: 'candidate_columns' when each candidate is a column and
        precincts are rows; 'candidate_rows' when each candidate is a row.
        grain: geographic grain of the data rows — 'precinct', 'district', or
        'county'.
        quirks: any of 'rotated_headers', 'stacked_contests', 'side_by_side',
        'multi_sheet_stitch'. Return an empty list if none apply. OCR-needed is
        not a quirk; it is implied by the scanned_pdf container.
        '''
        file_name: str = dspy.InputField()
        container: str = dspy.InputField()
        page_count: int = dspy.InputField()
        content_preview: str = dspy.InputField(desc='first rows of the first tabular page')
        orientation: Orientation = dspy.OutputField()
        grain: Grain = dspy.OutputField()
        quirks: list[Quirk] = dspy.OutputField(desc='subset of the allowed quirk labels')

    model: str = os.environ.get('OE2D_LM', 'bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0')
    try:
        language_model = dspy.LM(model)
        dspy.configure(lm=language_model)
        categorizer = dspy.Predict(SourceCategorizer)
        prediction = categorizer(
            file_name=signals['file_name'],
            container=signals['container'],
            page_count=signals['page_count'],
            content_preview=signals['content_preview'],
        )
    except Exception as err:
        print(f'LLM categorization unavailable: {err}', file=sys.stderr)
        return None

    return {
        'orientation': prediction.orientation,
        'grain': prediction.grain,
        'quirks': [q for q in prediction.quirks if q in QUIRKS],
    }


def categorize(path: str) -> dict:
    '''Categorize a source file, returning a plain JSON-serializable dict.'''
    file_name: str = os.path.basename(path)
    container: str = detect_container(path)
    pages: int = count_pages(path, container)
    preview: str = content_preview(path, container)
    name_grain: str = grain_from_name(file_name)

    signals: dict = {
        'file_name': file_name,
        'container': container,
        'page_count': pages,
        'content_preview': preview,
    }

    llm: dict | None = run_llm(signals) if _llm_enabled() else None
    if llm is not None:
        orientation: Orientation = llm['orientation']
        grain: Grain = llm['grain'] if llm['grain'] != 'unknown' else name_grain
        quirks: list[Quirk] = list(llm['quirks'])
    else:
        orientation = 'unknown'
        grain = name_grain
        quirks = []

    category = SourceCategory(
        path=path,
        file_name=file_name,
        container=container,
        page_count=pages,
        orientation=orientation,
        grain=grain,
        quirks=quirks,
        llm_used=llm is not None,
    )
    return category.model_dump()


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='Categorize an election-results source file.',
    )
    parser.add_argument('path', help='Path to the source file')
    args: argparse.Namespace = parser.parse_args()

    print(json.dumps(categorize(args.path), indent=2))


if __name__ == '__main__':
    main()
