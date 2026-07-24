'''Read one source unit's text as cheaply as possible (general-purpose).

Free first: structured text via source_table (spreadsheets, vector PDFs) and raw
lines for csv/txt. A PDF page with no extractable text (a scan) falls back to
LOCAL OCR (tesseract, free); paid OCR stays a concern for layers above this one.
This is the text counterpart to source_table's row reader -- oe2d.contests uses it
to locate contests without paid services. Not tabular; just "turn a unit into text".
'''
from __future__ import annotations

import os
import shutil
import subprocess

from . import source_table

# tesseract page-seg modes to union: 3 (default body text) + 11 (sparse text, which
# recovers isolated contest-title lines that mode 3 drops on dense results pages).
_TESSERACT_PSMS: tuple[int, ...] = (3, 11)
_TEXT_EXTS: tuple[str, ...] = ('.csv', '.txt')


def unit_text(path: str, unit: int) -> str:
    '''Best cheap text for a page/sheet: free structured text, else local OCR.'''
    ext: str = os.path.splitext(path)[1].lower()
    if ext in _TEXT_EXTS:
        return _raw_text(path)
    try:
        rows: list[list[str]] | None = source_table.page_table(path, unit)
    except Exception:
        rows = None
    if rows:
        return '\n'.join(' '.join(cell for cell in row if cell) for row in rows)
    if ext == '.pdf':
        try:
            words: list[dict] | None = source_table.page_words(path, unit)
        except Exception:
            words = None
        if words:
            return ' '.join(word['text'] for word in words)
        return ocr_page(path, unit)
    return ''


def ocr_page(path: str, unit: int, resolution: int = 300) -> str:
    '''Render a page/sheet to an image and OCR it locally with tesseract (free).'''
    from .categorize import rendering
    image_path: str = rendering.render_page(path, unit, resolution=resolution)
    return tesseract_text(image_path)


def tesseract_text(image_path: str) -> str:
    '''Union of tesseract reads across page-seg modes; empty if tesseract absent.'''
    if not shutil.which('tesseract'):
        return ''
    chunks: list[str] = []
    for psm in _TESSERACT_PSMS:
        try:
            result = subprocess.run(
                ['tesseract', image_path, 'stdout', '--psm', str(psm)],
                capture_output=True, text=True, timeout=120)
            chunks.append(result.stdout)
        except Exception:
            pass
    return '\n'.join(chunks)


def _raw_text(path: str, max_lines: int = 400) -> str:
    lines: list[str] = []
    try:
        with open(path, encoding='utf-8', errors='replace') as handle:
            for _, line in zip(range(max_lines), handle):
                lines.append(line.rstrip('\n'))
    except OSError:
        return ''
    return '\n'.join(lines)
