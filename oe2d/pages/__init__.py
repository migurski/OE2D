'''Analyze a single page image and report in-page facts for extraction.

Usage: oe2d-pages path/to/page.png 1
       oe2d-pages path/to/source.pdf 3

Prints a JSON dict of per-page properties a downstream extractor can route on:
candidate orientation (columns vs rows), whether contest names / candidate names
/ headers are visible on THIS page, the precinct scope and axis, and the page's
skew in degrees. This is a single-image DSPy program (the composite PageAnalyzer),
distinct from any inter-page table stitching, which happens at a different level.

skew_degrees is the one non-VLM field: a VLM can't estimate fine rotation from an
image, so PageAnalyzer measures it deterministically with oe2d.pages.deskew on the
same image.

A source that is not already an image is rendered to one first (a page of a PDF,
a sheet of a spreadsheet) via oe2d.rendering, so the same program
serves both raw page images and pages pulled from source files.

The program is a composite dspy.Module (PageAnalyzer): its forward() runs the VLM
content prediction AND measures skew deterministically in-module, so a page's full
per-page facts — the six VLM content fields plus skew_degrees — come back as one
prediction. Skew is not an LM output; it is computed by oe2d.pages.deskew on the
same image, folded into the program so there is no separate step to run.
'''
from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import os
import sys

import dotenv
import dspy
import pydantic
from PIL import Image

from .. import config
from . import deskew
from .signatures import CandidateOrientation, PageAnalysis, PrecinctAxis, PrecinctScope


# Extensions we treat as already-rendered page images; anything else is rendered.
_IMAGE_EXTS: tuple[str, ...] = ('.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff', '.bmp')

# Render source pages at this DPI for inference. High enough that the dense
# candidate-column tables stay legible (220 undersamples them); the committed
# training images are rendered density-tiered (300 dense / 220 sparse), and 300
# here keeps the demanding case matched.
INFERENCE_DPI: int = 300


class PageProperties(pydantic.BaseModel):
    '''In-page facts about a single rendered election-results page.

    The first six fields are VLM content predictions. skew_degrees is different:
    a VLM can't estimate fine (sub-2 deg) page rotation from an image — it defaults
    to ~0 regardless — so it is measured deterministically by oe2d.pages.deskew and
    folded in by the PageAnalyzer module. It is a program output but NOT a trained
    or scored field (see CONTENT_FIELDS / metrics.FIELD_WEIGHTS).
    '''
    candidate_orientation: CandidateOrientation
    contest_name_present: bool
    candidate_names_present: bool
    headers_present: bool
    precinct_scope: PrecinctScope
    precinct_orientation: PrecinctAxis
    # Detector-sourced (deskew), positive = counter-clockwise; NOT a VLM output.
    skew_degrees: float


# The VLM content fields — what the trained predictor emits and what datasets and
# metrics work over. Skew is excluded here on purpose (it is not learned).
CONTENT_FIELDS: tuple[str, ...] = (
    'candidate_orientation', 'contest_name_present', 'candidate_names_present',
    'headers_present', 'precinct_scope', 'precinct_orientation',
)

# Every field the composite program returns: the content fields plus the
# detector-sourced skew. Kept in the PageProperties order.
OUTPUT_FIELDS: tuple[str, ...] = CONTENT_FIELDS + ('skew_degrees',)


# The trained program, committed as package data. Loaded onto the predictor when
# present so an installed oe2d analyzes pages with the optimized prompt; absent,
# the stock prompt is used. optimize.py writes here.
OPTIMIZED_MODEL_PATH: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'model', 'optimized_page_analyzer.json')


def _instrument() -> None:
    '''Turn on cmpnd tracing when a key is configured; otherwise do nothing.

    Tags traces 'oe2d-pages'. Loads a
    repo-local .env explicitly rather than relying on litellm's import-time side
    effect, so the key source stays visible here.
    '''
    dotenv.load_dotenv()
    key: str | None = os.environ.get('CMPND_API_KEY')
    if not key:
        return
    try:
        import cmpnd
        cmpnd.configure(
            api_key=key,
            endpoint=os.environ.get('CMPND_ENDPOINT'),
            project_tags=['oe2d-pages'],
        )
        cmpnd.auto_instrument()
    except Exception:
        pass


def render_source(path: str, page: int = 1, member: str | None = None,
                  resolution: int = INFERENCE_DPI) -> str:
    '''Return a page image path: the file itself if already an image, else render.'''
    if os.path.splitext(path)[1].lower() in _IMAGE_EXTS:
        return path
    from .. import rendering
    return rendering.render_page(path, page, member, resolution=resolution)


def _image_to_pil(image: dspy.Image) -> Image.Image:
    '''Recover a PIL image from a dspy.Image so skew can be measured on it.

    A dspy.Image built from a path stores its bytes as a base64 data URI in .url
    (verified format 'data:image/png;base64,<b64>'); decode that. A .url that is a
    plain filesystem path (no data: scheme) is opened directly.
    '''
    url: str = image.url
    if url.startswith('data:'):
        _, _, encoded = url.partition(',')
        return Image.open(io.BytesIO(base64.b64decode(encoded)))
    return Image.open(url)


class PageAnalyzer(dspy.Module):
    '''The full per-page program: VLM content prediction plus deterministic skew.

    forward() runs the trained content predictor and, on the same image, measures
    skew with the projection-profile detector, returning both as one prediction.
    Skew adds no LM overhead — it is a direct numeric computation, not a tool the
    LM decides to call.
    '''
    def __init__(self) -> None:
        super().__init__()
        self.analyze: dspy.Module = dspy.Predict(PageAnalysis)

    def forward(self, image: dspy.Image) -> dspy.Prediction:
        content = self.analyze(image=image)
        skew: float = deskew.detect_skew_pil(_image_to_pil(image))
        return dspy.Prediction(
            candidate_orientation=content.candidate_orientation,
            contest_name_present=content.contest_name_present,
            candidate_names_present=content.candidate_names_present,
            headers_present=content.headers_present,
            precinct_scope=content.precinct_scope,
            precinct_orientation=content.precinct_orientation,
            skew_degrees=skew,
        )


def inference_lm() -> dspy.LM:
    '''The LM PageAnalyzer reads pages with: the shared Kimi K2 (multimodal) model at
    inference settings. A settled classifier, not GEPA-style exploration -- temperature 0
    keeps the six-field answer stable, and a large max_tokens leaves headroom so a page
    reasoned about at length doesn't truncate mid-answer (the Kimi 'code' model can repeat
    itself up to the cap at high temperature). Defined here, beside the program, so the
    module + signature + LM read together. A TRAINED artifact carries its OWN lm and
    OVERRIDES this on load (see build_analyzer) -- the artifact is authoritative.'''
    return dspy.LM(config.TASK_LM, temperature=0.0, max_tokens=8192)


def build_analyzer() -> PageAnalyzer:
    '''Construct the composite page analyzer. A trained artifact, when present, fully
    governs (its saved prompt AND lm win); otherwise bind the stock inference LM.'''
    analyzer: PageAnalyzer = PageAnalyzer()
    if os.path.exists(OPTIMIZED_MODEL_PATH):
        analyzer.load(OPTIMIZED_MODEL_PATH)
    else:
        analyzer.set_lm(inference_lm())
    return analyzer


def analyze_image(image_path: str) -> dict:
    '''Run the analyzer on an already-rendered page image; return a plain dict.'''
    _instrument()
    analyzer: PageAnalyzer = build_analyzer()
    prediction = analyzer(image=dspy.Image(image_path))
    properties = PageProperties(
        candidate_orientation=prediction.candidate_orientation,
        contest_name_present=prediction.contest_name_present,
        candidate_names_present=prediction.candidate_names_present,
        headers_present=prediction.headers_present,
        precinct_scope=prediction.precinct_scope,
        precinct_orientation=prediction.precinct_orientation,
        skew_degrees=float(prediction.skew_degrees),
    )
    return properties.model_dump()


def analyze_page(path: str, page: int = 1, member: str | None = None) -> dict:
    '''Analyze a page of a source file (rendering it first if it is not an image).'''
    return analyze_image(render_source(path, page, member))


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='Analyze a single election-results page image.',
    )
    parser.add_argument('path', help='A page image, or a source file to render')
    parser.add_argument('page', type=int,
                        help='1-based page/sheet to render from a source file; pass 1 for a raw page image')
    parser.add_argument('--member', help='Zip member to render (for zip sources)')
    parser.add_argument('-v', '--verbose', action='store_true', help='log LM steps')
    args: argparse.Namespace = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format='%(message)s')
        logging.getLogger('dspy').setLevel(logging.INFO)

    print(json.dumps(analyze_page(args.path, args.page, args.member), indent=2))


if __name__ == '__main__':
    main()
