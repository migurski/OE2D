'''Detect a page's skew angle deterministically, without an LM.

A vision LM cannot estimate fine (sub-2 deg) rotation from a page image — it just
answers ~0 — so skew is measured here with a classic projection-profile method:
rotate the (binarized) page over a range of candidate angles and pick the one
whose horizontal row-sum profile is sharpest. When text lines are level they pile
ink into a few rows, so the profile's row-to-row variation peaks; that angle is
the page's tilt.

Sign convention matches the training augmentations: a positive angle means the
page is rotated counter-clockwise, so rotating it by -angle straightens it.

CLI: oe2d-detect-skew path/to/page.png   (or a source file with --page)
'''
from __future__ import annotations

import argparse
import json
import os

import numpy as np
from PIL import Image

# Search only a small window — real document scans are barely tilted — first
# coarsely, then refine around the best coarse angle.
_MAX_ANGLE: float = 3.0
_COARSE_STEP: float = 0.5
_FINE_STEP: float = 0.05
# Downscale the long edge to this before searching; skew is a global property, so
# a smaller image gives the same angle far faster.
_WORK_EDGE: int = 1000


def _ink(image: 'Image.Image') -> np.ndarray:
    '''Binarize to a float array where 1.0 = ink (dark), 0.0 = background.'''
    gray: np.ndarray = np.asarray(image.convert('L'), dtype=np.float32)
    # Otsu-free fixed cut is enough for rendered pages: darker than mid-gray = ink.
    return (gray < 160).astype(np.float32)


def _sharpness(ink: np.ndarray, angle: float) -> float:
    '''How peaked the horizontal row-sum profile is after rotating by -angle.

    Rotating by -angle straightens a page tilted by +angle; a level page's rows of
    text then align, so the row sums vary sharply from line to gap. Measured as the
    sum of squared differences between adjacent row sums.
    '''
    rotated = Image.fromarray((ink * 255).astype(np.uint8)).rotate(
        -angle, resample=Image.BILINEAR, expand=False, fillcolor=0)
    profile: np.ndarray = np.asarray(rotated, dtype=np.float32).sum(axis=1)
    return float(np.sum(np.diff(profile) ** 2))


def _best_angle(ink: np.ndarray, center: float, half_width: float, step: float) -> float:
    '''Angle in [center-half_width, center+half_width] maximizing profile sharpness.'''
    angles = np.arange(center - half_width, center + half_width + step / 2, step)
    return float(max(angles, key=lambda a: _sharpness(ink, a)))


def detect_skew(image_path: str, max_angle: float = _MAX_ANGLE) -> float:
    '''Estimate a page image's skew in degrees (positive = counter-clockwise).

    Coarse-to-fine projection-profile search over [-max_angle, +max_angle].
    Returns the tilt; rotate the page by -result to straighten it.
    '''
    image: Image.Image = Image.open(image_path)
    if max(image.size) > _WORK_EDGE:
        scale: float = _WORK_EDGE / max(image.size)
        image = image.resize((max(1, int(image.width * scale)),
                              max(1, int(image.height * scale))))
    ink: np.ndarray = _ink(image)
    coarse: float = _best_angle(ink, 0.0, max_angle, _COARSE_STEP)
    return round(_best_angle(ink, coarse, _COARSE_STEP, _FINE_STEP), 2)


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='Detect a page image\'s skew in degrees (projection profile, no LM).',
    )
    parser.add_argument('path', help='A page image, or a source file to render')
    parser.add_argument('--page', type=int, default=1,
                        help='1-based page/sheet to render when given a source file')
    parser.add_argument('--member', help='Zip member to render (for zip sources)')
    parser.add_argument('--max-angle', type=float, default=_MAX_ANGLE,
                        help='Search window half-width in degrees')
    args: argparse.Namespace = parser.parse_args()

    from . import render_source
    image_path: str = render_source(args.path, args.page, args.member)
    print(json.dumps({'skew_degrees': detect_skew(image_path, args.max_angle)}, indent=2))


if __name__ == '__main__':
    main()
