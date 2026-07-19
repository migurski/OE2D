'''Render a single page (PDF) or sheet (spreadsheet) to a compressed PNG.

PDFs rasterize directly via pdfplumber. Office formats (xlsx, both .xls
flavors, csv, txt, docx) go through LibreOffice: soffice converts to PDF, then
the page is rasterized. For spreadsheets a single sheet is isolated first so a
requested sheet renders to its own page rather than paginating the whole book.
optipng shrinks the result. Zip members are streamed out to a temp file.
'''
from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
import tempfile
import zipfile

import openpyxl
import pdfplumber

from .. import categorize

RESOLUTION = 220

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


def _cache_dir() -> str:
    global _render_dir
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
    _profile_seq += 1
    profile: str = os.path.join(_cache_dir(), f'profile-{_profile_seq}')
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


def render_page(path: str, page: int = 1, member: str | None = None,
                resolution: int = RESOLUTION) -> str:
    '''Render one page (PDF) or sheet (spreadsheet) to a PNG; return its path.'''
    local: str = material_path(path, member)
    container: str = categorize.detect_container(local)
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
