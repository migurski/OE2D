'''Detect a page's skew angle deterministically, without an LM.

A vision LM cannot estimate fine (sub-2 deg) rotation from a page image — it just
answers ~0 — so skew is measured here with a classic projection-profile method
(Postl): rotate the (binarized) page over a range of candidate angles and, at
each, sum the SQUARED horizontal row sums. When text lines are level they pile
ink into few rows, so a few row sums get large and the sum of squares peaks; that
angle is the page's tilt.

Sign convention: a positive angle means the page is rotated counter-clockwise, so
rotating it by -angle straightens it.

Validated against hand-measured real scans, not just synthetic rotations,
including deliberately rough ones (real tilt, speckle noise, faint/low-contrast;
see oe2d-data/pages/deskew-scans/): MAE ~0.03 deg, max error ~0.07 deg across the
clean and rough sets. There is a sensitivity floor around ~0.3 deg on sparse,
noisy pages (mostly-whitespace continuation pages), where a smaller real tilt
reads as 0 — negligible for downstream OCR.
Note: Otsu thresholding was tried and is WORSE here — it mis-splits the gray
scan background and rails the search to the boundary; the fixed cut is better.

Used internally by oe2d.pages (PageAnalyzer measures skew per page); not a CLI.
'''
from __future__ import annotations

import os

import numpy as np
from PIL import Image

# Search only a small window — real document scans are barely tilted — first
# coarsely, then refine around the best coarse angle.
_MAX_ANGLE: float = 3.0
_COARSE_STEP: float = 0.5
_FINE_STEP: float = 0.05
# Downscale the long edge to this before searching; skew is a global property, so
# a smaller image gives the same angle far faster (1600 keeps small-angle signal).
_WORK_EDGE: int = 1600


def _ink(image: 'Image.Image') -> np.ndarray:
    '''Binarize to a float array where 1.0 = ink (dark), 0.0 = background.'''
    gray: np.ndarray = np.asarray(image.convert('L'), dtype=np.float32)
    # Otsu-free fixed cut is enough for rendered pages: darker than mid-gray = ink.
    return (gray < 160).astype(np.float32)


def _sharpness(ink: np.ndarray, angle: float) -> float:
    '''Postl objective: sum of squared row sums after rotating by -angle.

    Rotating by -angle straightens a page tilted by +angle; a level page's text
    rows then align, concentrating ink into few rows so a few row sums grow large
    and the sum of their squares peaks. (A sum-of-squared-adjacent-differences
    objective was tried and is markedly less sensitive to small real tilts.)
    '''
    rotated = Image.fromarray((ink * 255).astype(np.uint8)).rotate(
        -angle, resample=Image.BILINEAR, expand=False, fillcolor=0)
    profile: np.ndarray = np.asarray(rotated, dtype=np.float32).sum(axis=1)
    return float(np.sum(profile ** 2))


def _best_angle(ink: np.ndarray, center: float, half_width: float, step: float) -> float:
    '''Angle in [center-half_width, center+half_width] maximizing profile sharpness.'''
    angles = np.arange(center - half_width, center + half_width + step / 2, step)
    return float(max(angles, key=lambda a: _sharpness(ink, a)))


def detect_skew_pil(image: 'Image.Image', max_angle: float = _MAX_ANGLE) -> float:
    '''Estimate an already-loaded page image's skew in degrees (+ = counter-clockwise).

    Coarse-to-fine projection-profile search over [-max_angle, +max_angle].
    Returns the tilt; rotate the page by -result to straighten it. Works on a PIL
    image already in memory so the composite analyzer can measure skew on the same
    image it hands the VLM, without a second disk read.
    '''
    if max(image.size) > _WORK_EDGE:
        scale: float = _WORK_EDGE / max(image.size)
        image = image.resize((max(1, int(image.width * scale)),
                              max(1, int(image.height * scale))))
    ink: np.ndarray = _ink(image)
    coarse: float = _best_angle(ink, 0.0, max_angle, _COARSE_STEP)
    return round(_best_angle(ink, coarse, _COARSE_STEP, _FINE_STEP), 2)


def detect_skew(image_path: str, max_angle: float = _MAX_ANGLE) -> float:
    '''Estimate a page image's skew in degrees (positive = counter-clockwise).

    Thin wrapper over detect_skew_pil that opens the file first.
    '''
    return detect_skew_pil(Image.open(image_path), max_angle)
