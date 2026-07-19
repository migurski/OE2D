# Trained categorizer

`optimized_categorizer.json` is the GEPA-optimized `SourceCategorizer` program.
It is committed package data: the CLI (`oe2d-categorize-source`) loads it
automatically when present, so an installed `oe2d` ships an already-optimized
categorizer. Without it, the categorizer runs on the stock prompt.

Produced by `python -m oe2d.categorize.optimize` (which writes here by default),
using the gold set in `oe2d-data/labels/` and the fixtures in
`oe2d-data/fixtures/`. Neither of those data trees ships in the wheel — only
this trained artifact does.
