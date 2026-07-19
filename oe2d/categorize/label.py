'''Guided labeling of categorization fixtures into a gold JSONL set.

Usage: oe2d-label-categories [--fixtures DIR] [--out FILE] [--redo]

Walks each fixture, pre-fills the deterministic fields (container, page_count,
grain hint), shows a content preview, and prompts for the judgment fields
(orientation, layout properties, and grain where the name is silent). Scanned PDFs and
other bitmap sources have no text preview, so `o` opens the file in your OS
viewer. Records append to labels/category.jsonl as you go, and already-labeled
fixtures are skipped on the next run unless --redo is given.
'''
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

import openpyxl

from .. import source_table
from .. import categorize

_ORIENTATIONS = {'c': 'candidate_columns', 'r': 'candidate_rows', 'u': 'unknown'}
_GRAINS = {'p': 'precinct', 'd': 'district', 'c': 'county', 'u': 'unknown'}
_READABLE = ('vector_pdf', 'xlsx', 'xls_binary', 'xls_xml', 'csv', 'txt')

# Spreadsheets render badly in Quick Look; hand them to the default app instead.
_APP_CONTAINERS = ('xlsx', 'xls_binary', 'xls_xml')

# .xls (especially XML SpreadsheetML) opens illegibly in Numbers; convert these
# to a temporary .xlsx for viewing while the fixture on disk stays .xls.
_CONVERT_CONTAINERS = ('xls_binary', 'xls_xml')
_VIEW_MAX_SHEETS = 40
_VIEW_MAX_ROWS = 300


def iter_targets(fixtures_dir: str) -> list[str]:
    '''List fixture files to label, skipping READMEs and hidden files.'''
    targets: list[str] = []
    for name in sorted(os.listdir(fixtures_dir)):
        full: str = os.path.join(fixtures_dir, name)
        if name.startswith('.') or name.lower().endswith('.md'):
            continue
        if os.path.isfile(full):
            targets.append(full)
    return targets


def load_done(out_path: str) -> dict[str, dict]:
    '''Read already-labeled records keyed by fixture path.'''
    done: dict[str, dict] = {}
    if os.path.exists(out_path):
        with open(out_path, encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if line:
                    record: dict = json.loads(line)
                    done[record['path']] = record
    return done


def append_record(out_path: str, record: dict) -> None:
    '''Append one gold record as a JSONL line.'''
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'a', encoding='utf-8') as handle:
        handle.write(json.dumps(record) + '\n')


def format_preview(path: str, container: str, rows: int = 15, cols: int = 14) -> str | None:
    '''Render a compact text preview of the first page, or None if not text.'''
    if container not in _READABLE:
        return None
    if container in ('csv', 'txt'):
        lines: list[str] = []
        with open(path, encoding='utf-8', errors='replace') as handle:
            for _, line in zip(range(rows), handle):
                lines.append(line.rstrip('\n')[:120])
        return '\n'.join(lines) or None
    try:
        table: list[list[str]] | None = source_table.page_table(path, 1)
    except Exception:
        table = None
    if not table:
        return None
    return '\n'.join(
        ' | '.join((cell or '')[:18] for cell in row[:cols]) for row in table[:rows]
    )


class Previewer:
    '''Show each fixture in a rendered viewer, one window at a time.

    PDFs use macOS Quick Look (`qlmanage -p`) in the background so the window
    stays up while you answer and is dismissed before the next fixture.
    Spreadsheets render badly in Quick Look, so they open in the default app
    (`open`) instead. Non-Mac runs fall back to the text preview.
    '''

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None

    def show(self, path: str, container: str) -> bool:
        '''Preview path; return whether a viewer was launched.'''
        self.close()
        if sys.platform != 'darwin':
            return False
        if container in _APP_CONTAINERS:
            if shutil.which('open'):
                subprocess.run(['open', path], check=False)
                return True
            return False
        if shutil.which('qlmanage'):
            self.proc = subprocess.Popen(
                ['qlmanage', '-p', path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
        return False

    def reopen(self, path: str, container: str) -> None:
        '''Explicit open in response to `o`.'''
        if self.show(path, container):
            return
        opener: str = 'open' if sys.platform == 'darwin' else 'xdg-open'
        if shutil.which(opener):
            subprocess.run([opener, path], check=False)
        else:
            print(f'  (view manually: {path})')

    def close(self) -> None:
        '''Dismiss the current Quick Look window, if any.'''
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None


def _ask_orientation(path: str, container: str, previewer: Previewer) -> str | None:
    '''Prompt for orientation; None means skip, and 'QUIT' aborts the run.'''
    while True:
        reply: str = input('  candidate orientation [c=candidates in columns  r=candidates in rows  u=unknown | o=open s=skip q=quit]: ').strip().lower()
        if reply == 'o':
            previewer.reopen(path, container)
            continue
        if reply == 's':
            return None
        if reply == 'q':
            return 'QUIT'
        if reply in _ORIENTATIONS:
            return _ORIENTATIONS[reply]
        print('  ? enter c, r, u, o, s, or q')


def _ask_grain(hint: str) -> str:
    reply: str = input(f'  grain [p d c u] (Enter={hint}): ').strip().lower()
    return _GRAINS.get(reply, hint) if reply else hint


def _ask_layout() -> dict[str, bool]:
    '''Ask which layout properties apply; return them as has_* boolean flags.'''
    for index, name in enumerate(categorize.LAYOUT_PROPERTIES, 1):
        print(f'    {index}) {name} — {categorize.LAYOUT_PROPERTY_DESCRIPTIONS[name]}')
    reply: str = input('  layout properties (comma nums, Enter=none): ').strip()
    flags: dict[str, bool] = {name: False for name in categorize.LAYOUT_PROPERTIES}
    for token in reply.split(','):
        token = token.strip()
        if token.isdigit() and 1 <= int(token) <= len(categorize.LAYOUT_PROPERTIES):
            flags[categorize.LAYOUT_PROPERTIES[int(token) - 1]] = True
    return flags


def _ask_container(container: str) -> str:
    reply: str = input(f'  container={container} (Enter=keep, or type override): ').strip()
    if reply and reply not in categorize.CONTAINERS:
        print(f'  note: "{reply}" is not in {categorize.CONTAINERS}')
    return reply or container


def convert_to_xlsx(path: str, work_dir: str) -> str:
    '''Render every sheet of an .xls into a temporary .xlsx for legible viewing.'''
    sheet_count: int = min(categorize.count_pages(path, categorize.detect_container(path)),
                           _VIEW_MAX_SHEETS)
    workbook: openpyxl.Workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for page in range(1, sheet_count + 1):
        rows: list[list[str]] | None = source_table.page_table(path, page)
        sheet = workbook.create_sheet(f'Sheet{page}')
        for row in (rows or [])[:_VIEW_MAX_ROWS]:
            sheet.append(['' if cell is None else str(cell) for cell in row])
    out_path: str = os.path.join(work_dir, 'view.xlsx')
    workbook.save(out_path)
    return out_path


def view_for(path: str, container: str, work_dir: str) -> tuple[str, str]:
    '''Return (path, container) to preview, converting .xls to .xlsx on Mac.'''
    if sys.platform == 'darwin' and container in _CONVERT_CONTAINERS:
        try:
            return convert_to_xlsx(path, work_dir), 'xlsx'
        except Exception:
            return path, container
    return path, container


def label_one(path: str, previewer: Previewer, work_dir: str) -> dict | str | None:
    '''Interactively label one fixture. Returns a record, None (skip), or 'QUIT'.'''
    container: str = categorize.detect_container(path)
    pages: int = categorize.count_pages(path, container)
    grain_hint: str = categorize.grain_from_name(os.path.basename(path))

    print(f'\n{os.path.basename(path)}')
    print(f'  container={container}  page_count={pages}  grain_hint={grain_hint}')
    view_path, view_container = view_for(path, container, work_dir)
    if previewer.show(view_path, view_container):
        note: str = ' (converted to .xlsx)' if view_path != path else ''
        print(f'  (preview opened{note} — o to reopen)')
    else:
        preview: str | None = format_preview(path, container)
        if preview:
            print('  --- preview ---')
            for line in preview.splitlines():
                print('  ' + line)
        else:
            print('  (no preview available — o to open the file)')

    orientation: str | None = _ask_orientation(view_path, view_container, previewer)
    if orientation == 'QUIT':
        return 'QUIT'
    if orientation is None:
        return None
    grain: str = _ask_grain(grain_hint)
    layout: dict[str, bool] = _ask_layout()
    container = _ask_container(container)

    return {
        'path': path,
        'container': container,
        'orientation': orientation,
        'grain': grain,
        **layout,
    }


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='Guided labeling of categorization fixtures.',
    )
    parser.add_argument('--fixtures', default='oe2d-data/fixtures/categorize', help='Fixtures directory')
    parser.add_argument('--out', default='oe2d-data/labels/category.jsonl', help='Gold JSONL output')
    parser.add_argument('--redo', action='store_true', help='Relabel fixtures already in the output')
    args: argparse.Namespace = parser.parse_args()

    targets: list[str] = iter_targets(args.fixtures)
    done: dict[str, dict] = {} if args.redo else load_done(args.out)
    pending: list[str] = [t for t in targets if t not in done]

    print(f'{len(targets)} fixtures, {len(done)} already labeled, {len(pending)} to go.')
    previewer: Previewer = Previewer()
    with tempfile.TemporaryDirectory() as work_dir:
        try:
            for index, path in enumerate(pending, 1):
                print(f'\n[{index}/{len(pending)}]', end='')
                outcome: dict | str | None = label_one(path, previewer, work_dir)
                if outcome == 'QUIT':
                    print('\nSaved progress. Re-run to continue.')
                    return
                if outcome is None:
                    print('  skipped.')
                    continue
                append_record(args.out, outcome)
            print('\nDone.')
        finally:
            previewer.close()


if __name__ == '__main__':
    main()
