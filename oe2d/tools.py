'''Host-side tools for the RLM categorizer.

Every tool returns a SIMPLE_TYPE (str, int, float, bool, list, dict, None) so
its result crosses the Deno/Pyodide sandbox boundary intact. Image data never
crosses: inspect_page runs a vision model host-side and returns text.
'''
from __future__ import annotations

import zipfile

import source_table

from . import categorize, inspector, rendering

# Keep tabular returns bounded so a wide sheet does not flood the REPL output.
_MAX_ROWS = 100
_MAX_COLS = 60

# Returned for a PDF page with no extractable text, so the model reaches for the
# vision tool instead of concluding the page is empty and guessing.
_NO_TEXT_HINT = ('[no extractable text on this page — it is likely scanned; call '
                 'inspect_page(path, page) to read it as an image]')


def zip_members(path: str) -> list[str]:
    '''List the file names inside a .zip source.'''
    with zipfile.ZipFile(path) as archive:
        return [name for name in archive.namelist() if not name.endswith('/')]


# member is keyword-only: it names a file inside a .zip and is rarely needed, so
# a stray positional (e.g. the container) cannot accidentally land there.
def page_count(path: str, *, member: str | None = None) -> int:
    '''Number of pages (PDF) or sheets (spreadsheet) in a source or zip member.'''
    local: str = rendering.material_path(path, member)
    return source_table.page_count(local)


def page_table(path: str, page: int, *, member: str | None = None) -> list[list[str]]:
    '''Parsed rows of one page/sheet as lists of strings; empty if unreadable.

    For spreadsheets page is the sheet number. Returns nothing for scanned
    PDFs (no text) — use inspect_page to view those.
    '''
    local: str = rendering.material_path(path, member)
    rows: list[list[str]] | None = source_table.page_table(local, page)
    if rows:
        return [row[:_MAX_COLS] for row in rows[:_MAX_ROWS]]
    if categorize.detect_container(local) in ('vector_pdf', 'scanned_pdf'):
        return [[_NO_TEXT_HINT]]
    return []


def page_words(path: str, page: int, *, member: str | None = None) -> list[dict]:
    '''Words with positions on a PDF page; empty for non-PDF or textless pages.'''
    local: str = rendering.material_path(path, member)
    words: list[dict] | None = source_table.page_words(local, page)
    if words:
        return words
    if categorize.detect_container(local) in ('vector_pdf', 'scanned_pdf'):
        return [{'text': _NO_TEXT_HINT}]
    return []


def inspect_page(path: str, page: int = 1, question: str = '', *, member: str | None = None) -> str:
    '''View a rendered page/sheet with a vision model and return observed facts.

    Required for scanned PDFs, which have no extractable text. Also use to
    confirm layout: candidates in columns vs rows, rotated headers, stacked or
    side-by-side contests. For spreadsheets page is the sheet number; look past
    a table-of-contents sheet at an actual contest sheet.
    '''
    return inspector.inspect_page(path, page, member=member, question=question)
