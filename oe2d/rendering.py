'''Render a single page (PDF) or sheet (spreadsheet) to a compressed PNG.

PDFs rasterize directly via pdfplumber. Office formats (xlsx, both .xls
flavors, csv, txt, docx) go through LibreOffice: soffice converts to PDF, then
the page is rasterized. For spreadsheets a single sheet is isolated first so a
requested sheet renders to its own page rather than paginating the whole book.
optipng shrinks the result. Zip members are streamed out to a temp file.

This rasterization backs the oe2d.pages analyzer (which renders a source page
before reading it) and is exposed as a CLI (oe2d-rendering) so a page image can
be pulled out of any source by hand.
'''
from __future__ import annotations

import argparse
import functools
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile

import openpyxl
import pdfplumber

RESOLUTION = 220

# Container formats recognized from a file extension alone (no content sniffing).
_EXT_CONTAINERS: dict[str, str] = {'.xlsx': 'xlsx', '.csv': 'csv', '.txt': 'txt', '.zip': 'zip'}


def detect_container(path: str) -> str:
    '''Sniff the container format from extension and file content.'''
    ext: str = os.path.splitext(path)[1].lower()
    if ext == '.pdf':
        return _detect_pdf_kind(path)
    if ext == '.xls':
        return _detect_xls_kind(path)
    return _EXT_CONTAINERS.get(ext, 'unknown')


def _detect_pdf_kind(path: str) -> str:
    '''Distinguish a vector PDF (extractable text) from a scanned bitmap.'''
    pdf: pdfplumber.PDF = pdfplumber.open(path)
    try:
        char_total: int = sum(len(page.chars) for page in pdf.pages[:5])
    finally:
        pdf.close()
    return 'vector_pdf' if char_total > 20 else 'scanned_pdf'


def _detect_xls_kind(path: str) -> str:
    '''Distinguish a binary BIFF .xls from an XML SpreadsheetML .xls.'''
    with open(path, 'rb') as file:
        head: bytes = file.read(20)
    if head.lstrip(b'\xef\xbb\xbf').startswith(b'<?xml'):
        return 'xls_xml'
    return 'xls_binary'

# soffice lives on PATH on Linux; on a Homebrew macOS host it is inside the app
# bundle and not on PATH, so probe the known locations too.
_SOFFICE_CANDIDATES = (
    'soffice', 'libreoffice',
    '/Applications/LibreOffice.app/Contents/MacOS/soffice',
    '/opt/homebrew/bin/soffice',
    '/usr/local/bin/soffice',
    '/usr/bin/soffice',
)

_render_dir: str | None = None
_profile_seq: int = 0

# Guards the shared cache-dir/profile counter against concurrent GEPA rollouts.
_state_lock = threading.Lock()

# pdfplumber's to_image() rasterizes through pdfium, a native library that is
# NOT thread-safe: two concurrent calls segfault. Serialize all rasterization
# behind this lock. Rendering is not the bottleneck (LM calls are), so this
# costs little while GEPA runs rollouts across threads.
_raster_lock = threading.Lock()


def _cache_dir() -> str:
    global _render_dir
    with _state_lock:
        if _render_dir is None:
            _render_dir = tempfile.mkdtemp(prefix='oe2d-render-')
        return _render_dir


def _safe(name: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]+', '-', name).strip('-') or 'file'


@functools.lru_cache(maxsize=1)
def find_soffice() -> str | None:
    '''Locate the LibreOffice binary on Linux or a macOS Homebrew host.'''
    for candidate in _SOFFICE_CANDIDATES:
        if os.path.sep in candidate:
            if os.path.exists(candidate):
                return candidate
        else:
            found: str | None = shutil.which(candidate)
            if found:
                return found
    return None


def _soffice_convert(src: str, out_format: str, out_dir: str) -> str:
    '''Convert src to out_format via LibreOffice; return the output path.

    Each call uses its own UserInstallation profile so concurrent conversions
    do not collide on the shared soffice profile lock.
    '''
    global _profile_seq
    soffice: str | None = find_soffice()
    if soffice is None:
        raise RuntimeError(
            'LibreOffice not found; install it (brew install --cask libreoffice) '
            'or put soffice on PATH'
        )
    with _state_lock:
        _profile_seq += 1
        seq: int = _profile_seq
    profile: str = os.path.join(_cache_dir(), f'profile-{seq}')
    subprocess.run(
        [soffice, '--headless', f'-env:UserInstallation=file://{profile}',
         '--convert-to', out_format, '--outdir', out_dir, src],
        env=dict(os.environ, HOME=_cache_dir()),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180, check=True,
    )
    stem: str = os.path.splitext(os.path.basename(src))[0]
    return os.path.join(out_dir, f'{stem}.{out_format.split(":")[0]}')


def material_path(path: str, member: str | None = None) -> str:
    '''Return a real local path for a source, extracting a zip member if named.'''
    if not member:
        return path
    dest: str = os.path.join(_cache_dir(), _safe(member))
    if not os.path.exists(dest):
        with zipfile.ZipFile(path) as archive:
            data: bytes = archive.read(member)
        with open(dest, 'wb') as handle:
            handle.write(data)
    return dest


@functools.lru_cache(maxsize=64)
def _ensure_xlsx(path: str, container: str) -> str:
    '''Return an .xlsx path for a workbook, converting .xls via LibreOffice.'''
    if container == 'xlsx':
        return path
    return _soffice_convert(path, 'xlsx', _cache_dir())


def _extract_sheet(xlsx_path: str, sheet: int) -> str:
    '''Write a one-sheet workbook (merges preserved) for the given sheet index.'''
    workbook: openpyxl.Workbook = openpyxl.load_workbook(xlsx_path)
    index: int = max(1, min(sheet, len(workbook.worksheets))) - 1
    keep = workbook.worksheets[index]
    for worksheet in list(workbook.worksheets):
        if worksheet is not keep:
            workbook.remove(worksheet)
    out_path: str = os.path.join(_cache_dir(), f'sheet-{index + 1}.xlsx')
    workbook.save(out_path)
    return out_path


def _raster_pdf(pdf_path: str, page: int, out_png: str, resolution: int) -> None:
    # pdfium is not thread-safe; hold the lock across the whole open/raster/save.
    with _raster_lock:
        pdf: pdfplumber.PDF = pdfplumber.open(pdf_path)
        try:
            index: int = max(1, min(page, len(pdf.pages))) - 1
            pdf.pages[index].to_image(resolution=resolution).save(out_png)
        finally:
            pdf.close()


def _optipng(png_path: str) -> None:
    optipng: str | None = shutil.which('optipng')
    if optipng:
        subprocess.run([optipng, '-quiet', '-o2', png_path],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def render_page(path: str, page: int, member: str | None = None,
                resolution: int = RESOLUTION) -> str:
    '''Render one page (PDF) or sheet (spreadsheet) to a PNG; return its path.'''
    local: str = material_path(path, member)
    container: str = detect_container(local)
    out_png: str = os.path.join(
        _cache_dir(), f'{_safe(member or os.path.basename(path))}-p{page}.png')

    if container in ('vector_pdf', 'scanned_pdf'):
        _raster_pdf(local, page, out_png, resolution)
    elif container in ('xlsx', 'xls_binary', 'xls_xml'):
        single: str = _extract_sheet(_ensure_xlsx(local, container), page)
        _raster_pdf(_soffice_convert(single, 'pdf', _cache_dir()), 1, out_png, resolution)
    elif container in ('csv', 'txt', 'docx', 'unknown'):
        _raster_pdf(_soffice_convert(local, 'pdf', _cache_dir()), page, out_png, resolution)
    else:
        raise ValueError(f'cannot render container: {container}')

    _optipng(out_png)
    return out_png


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='Render one page (PDF) or sheet (spreadsheet) of a source to a PNG.',
    )
    parser.add_argument('source', help='Source file path')
    parser.add_argument('page', type=int,
                        help='1-based page (PDF) or sheet (spreadsheet) to render')
    parser.add_argument('--member', help='Zip member to render (for zip sources)')
    parser.add_argument('--resolution', type=int, default=RESOLUTION,
                        help='Rasterization DPI')
    parser.add_argument('--out', help='Output PNG path (default: alongside the source)')
    args: argparse.Namespace = parser.parse_args()

    png_path: str = render_page(args.source, args.page, args.member, args.resolution)
    if args.out:
        shutil.copyfile(png_path, args.out)
        out_path: str = args.out
    else:
        stem: str = _safe(args.member or os.path.basename(args.source))
        out_path = f'{stem}-p{args.page}.png'
        shutil.copyfile(png_path, out_path)
    print(f'{out_path} ({os.path.getsize(out_path)} bytes)', file=sys.stderr)
    print(out_path)


if __name__ == '__main__':
    main()
