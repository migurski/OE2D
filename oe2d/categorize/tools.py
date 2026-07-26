'''Host-side tools for the RLM categorizer.

Every tool returns a SIMPLE_TYPE (str, int, float, bool, list, dict, None) so
its result crosses the Deno/Pyodide sandbox boundary intact.
'''
from __future__ import annotations

import zipfile

from .. import source_table

from .. import rendering
from .. import categorize

# Keep tabular returns bounded so a wide sheet does not flood the REPL output.
_MAX_ROWS = 100
_MAX_COLS = 60

# Returned for a PDF page with no extractable text, so the model records that the
# page is likely scanned rather than concluding it is empty and guessing.
_NO_TEXT_HINT = '[no extractable text on this page — it is likely scanned]'


def zip_members(path: str) -> list[str]:
    '''List the file names inside a .zip source.'''
    with zipfile.ZipFile(path) as archive:
        return [name for name in archive.namelist() if not name.endswith('/')]


# Named count_pages, not page_count, so it does not collide with the RLM's
# page_count input field — a same-named input would shadow the tool in the
# sandbox namespace, leaving the model unable to call it (it sees an int).
# member is keyword-only: it names a file inside a .zip and is rarely needed, so
# a stray positional (e.g. the container) cannot accidentally land there.
def count_pages(path: str, *, member: str | None = None) -> int:
    '''Number of pages (PDF) or sheets (spreadsheet) in a source or zip member.'''
    local: str = rendering.material_path(path, member)
    return source_table.page_count(local)


def page_table(path: str, page: int, *, member: str | None = None) -> list[list[str]]:
    '''Parsed rows of one page/sheet as lists of strings; empty if unreadable.

    For spreadsheets page is the sheet number. A scanned PDF has no extractable
    text, so it returns the no-text hint rather than rows.
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
