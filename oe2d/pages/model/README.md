# oe2d.pages trained model

`optimize.py` writes a GEPA-optimized page analyzer here as
`optimized_page_analyzer.json`. When that file is present it is committed as
package data and auto-loaded (its saved prompt AND lm win — see
`oe2d.pages.build_analyzer`); when absent, the stock program runs on the default
inference LM (`LM_QWEN3_VL`, temperature 0).

**No artifact ships today, by design.** GEPA was run and confirmed a no-op on this
saturated Qwen seed — it lifted a biased 20-page val split but scored no better
(slightly worse on `value_columns`) on the honest full set, so its output was
discarded (see `pages-PERFORMANCE.md`). The stock program IS the shipped program,
so Python is the single source of truth and there is no redundant stock-prompt
JSON to keep in sync. The load-if-present hook stays for a future optimization
that actually lifts.

The training images and labels live in the top-level `oe2d-data/pages/` tree and
are not packaged. See `oe2d-data/pages/README.md` for the dataset.
