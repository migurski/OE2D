# oe2d.pages trained model

`optimize.py` writes the GEPA-optimized page analyzer here as
`optimized_page_analyzer.json`. When that file is present it is committed as
package data and auto-loaded by `oe2d-analyze-page` (see
`oe2d.pages.build_analyzer`), so an installed `oe2d` analyzes pages with the
trained prompt; when absent, the stock prompt is used.

This is the only part of the page-analysis effort that ships in the wheel — the
training images and labels live in the top-level `oe2d-data/pages/` tree and are
not packaged. See `oe2d-data/pages/README.md` for the dataset.
