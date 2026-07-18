'''Guided labeling of categorization fixtures into a gold JSONL set.

Usage: oe2d-label-categories [--fixtures DIR] [--out FILE] [--redo]

Walks each fixture, pre-fills the deterministic fields (container, page_count,
grain hint), shows a content preview, and prompts for the judgment fields
(orientation, quirks, and grain where the name is silent). Scanned PDFs and
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

import source_table
from oe2d import categorize

_ORIENTATIONS = {'c': 'candidate_columns', 'r': 'candidate_rows', 'u': 'unknown'}
_GRAINS = {'p': 'precinct', 'd': 'district', 'c': 'county', 'u': 'unknown'}
_READABLE = ('vector_pdf', 'xlsx', 'xls_binary', 'xls_xml', 'csv', 'txt')


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

    Prefers macOS Quick Look (`qlmanage -p`) launched in the background so the
    preview window stays up while you answer the prompts; the previous window
    is dismissed before the next fixture. Falls back to the OS opener elsewhere.
    '''

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None

    def show(self, path: str) -> bool:
        '''Auto-preview via background Quick Look; True only if a window opened.

        Restricted to macOS `qlmanage` so non-Mac runs fall back to the text
        preview rather than a non-rendering opener.
        '''
        self.close()
        if sys.platform == 'darwin' and shutil.which('qlmanage'):
            self.proc = subprocess.Popen(
                ['qlmanage', '-p', path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
        return False

    def reopen(self, path: str) -> None:
        '''Explicit open in response to `o`: Quick Look, else the OS opener.'''
        if self.show(path):
            return
        opener: str = 'open' if sys.platform == 'darwin' else 'xdg-open'
        if shutil.which(opener):
            subprocess.run([opener, path], check=False)
        else:
            print(f'  (view manually: {path})')

    def close(self) -> None:
        '''Dismiss the current preview window, if any.'''
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None


def _ask_orientation(path: str, previewer: Previewer) -> str | None:
    '''Prompt for orientation; None means skip, and 'QUIT' aborts the run.'''
    while True:
        reply: str = input('  candidate orientation [c=candidates in columns  r=candidates in rows  u=unknown | o=open s=skip q=quit]: ').strip().lower()
        if reply == 'o':
            previewer.reopen(path)
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


def _ask_quirks() -> list[str]:
    for index, quirk in enumerate(categorize.QUIRKS, 1):
        print(f'    {index}) {quirk}')
    reply: str = input('  quirks (comma nums, Enter=none): ').strip()
    picks: list[str] = []
    for token in reply.split(','):
        token = token.strip()
        if token.isdigit() and 1 <= int(token) <= len(categorize.QUIRKS):
            picks.append(categorize.QUIRKS[int(token) - 1])
    return picks


def _ask_container(container: str) -> str:
    reply: str = input(f'  container={container} (Enter=keep, or type override): ').strip()
    if reply and reply not in categorize.CONTAINERS:
        print(f'  note: "{reply}" is not in {categorize.CONTAINERS}')
    return reply or container


def label_one(path: str, previewer: Previewer) -> dict | str | None:
    '''Interactively label one fixture. Returns a record, None (skip), or 'QUIT'.'''
    container: str = categorize.detect_container(path)
    pages: int = categorize.count_pages(path, container)
    grain_hint: str = categorize.grain_from_name(os.path.basename(path))

    print(f'\n{os.path.basename(path)}')
    print(f'  container={container}  page_count={pages}  grain_hint={grain_hint}')
    if previewer.show(path):
        print('  (Quick Look preview opened — o to reopen)')
    else:
        preview: str | None = format_preview(path, container)
        if preview:
            print('  --- preview ---')
            for line in preview.splitlines():
                print('  ' + line)
        else:
            print('  (no preview available — o to open the file)')

    orientation: str | None = _ask_orientation(path, previewer)
    if orientation == 'QUIT':
        return 'QUIT'
    if orientation is None:
        return None
    grain: str = _ask_grain(grain_hint)
    quirks: list[str] = _ask_quirks()
    container = _ask_container(container)

    return {
        'path': path,
        'container': container,
        'orientation': orientation,
        'grain': grain,
        'quirks': quirks,
    }


def main() -> None:
    os.environ.setdefault('OE2D_NO_LM', '1')
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='Guided labeling of categorization fixtures.',
    )
    parser.add_argument('--fixtures', default='oe2d/tests/fixtures', help='Fixtures directory')
    parser.add_argument('--out', default='oe2d/labels/category.jsonl', help='Gold JSONL output')
    parser.add_argument('--redo', action='store_true', help='Relabel fixtures already in the output')
    args: argparse.Namespace = parser.parse_args()

    targets: list[str] = iter_targets(args.fixtures)
    done: dict[str, dict] = {} if args.redo else load_done(args.out)
    pending: list[str] = [t for t in targets if t not in done]

    print(f'{len(targets)} fixtures, {len(done)} already labeled, {len(pending)} to go.')
    previewer: Previewer = Previewer()
    try:
        for index, path in enumerate(pending, 1):
            print(f'\n[{index}/{len(pending)}]', end='')
            outcome: dict | str | None = label_one(path, previewer)
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
