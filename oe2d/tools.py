'''Host-side tools for the RLM categorizer.

Every tool returns a SIMPLE_TYPE (str, int, float, bool, list, dict, None) so
its result crosses the Deno/Pyodide sandbox boundary intact. Image data never
crosses: inspect_page runs a vision model host-side and returns text.
'''
from __future__ import annotations

import zipfile

import source_table

from . import inspector, rendering

# Keep tabular returns bounded so a wide sheet does not flood the REPL output.
_MAX_ROWS = 100
_MAX_COLS = 60


def zip_members(path: str) -> list[str]:
    '''List the file names inside a .zip source.'''
    with zipfile.ZipFile(path) as archive:
        return [name for name in archive.namelist() if not name.endswith('/')]


def page_count(path: str, member: str | None = None) -> int:
    '''Number of pages (PDF) or sheets (spreadsheet) in a source or zip member.'''
    local: str = rendering.material_path(path, member)
    return source_table.page_count(local)


def page_table(path: str, page: int, member: str | None = None) -> list[list[str]]:
    '''Parsed rows of one page/sheet as lists of strings; empty if unreadable.

    For spreadsheets page is the sheet number. Returns nothing for scanned
    PDFs (no text) — use inspect_page to view those.
    '''
    local: str = rendering.material_path(path, member)
    rows: list[list[str]] | None = source_table.page_table(local, page)
    if not rows:
        return []
    return [row[:_MAX_COLS] for row in rows[:_MAX_ROWS]]


def page_words(path: str, page: int, member: str | None = None) -> list[dict]:
    '''Words with positions on a PDF page; empty for non-PDF or textless pages.'''
    local: str = rendering.material_path(path, member)
    words: list[dict] | None = source_table.page_words(local, page)
    return words or []


def inspect_page(path: str, page: int = 1, member: str | None = None, question: str = '') -> str:
    '''View a rendered page/sheet with a vision model and return observed facts.

    Required for scanned PDFs, which have no extractable text. Also use to
    confirm layout: candidates in columns vs rows, rotated headers, stacked or
    side-by-side contests. For spreadsheets page is the sheet number; look past
    a table-of-contents sheet at an actual contest sheet.
    '''
    return inspector.inspect_page(path, page, member, question)
