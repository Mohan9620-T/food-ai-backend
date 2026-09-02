# Vision evaluation dataset

This fixed regression set contains nine openly licensed food photographs from Wikimedia Commons
and nine deterministic difficult-condition variants, for 18 evaluated image files. It emphasizes
Indian dishes because that is the application's primary difficult-food context. Derived entries are
explicitly linked to their source with `derived_from`; they must not be counted as independent
photographs when interpreting aggregate results.

`labels.json` is the source of truth for expected labels, difficulty, known visual challenges,
creator attribution, license, and source URL. Images are stored in `images/` at a maximum width of
1280 pixels. Do not replace or add images during routine prompt/model comparisons; propose a
versioned dataset change instead so results remain comparable.

Evaluation should accept only the labels in `expected_items` as confident identifications. Items in
`acceptable_context` may be mentioned but are not required. A model should express uncertainty
rather than inventing a specific dish when evidence is insufficient.

Run `powershell -ExecutionPolicy Bypass -File scripts/download_vision_eval_dataset.ps1` from the
repository root to restore missing source images, then run
`.venv\Scripts\python.exe scripts/build_vision_eval_variants.py` to rebuild the derived cases.
