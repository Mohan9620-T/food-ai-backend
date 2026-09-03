# Phase 10 vision regression

Date: 2026-09-03

## Test suites

- Backend: 73 passed.
- Frontend: 8 files and 27 tests passed.
- Angular production build: passed; three static routes prerendered.

The initial frontend test attempt failed with `spawn EPERM` when the managed Windows sandbox blocked
esbuild's child process. The identical command passed outside that sandbox; there was no source or
test assertion failure.

## Baseline rerun method

The current `ChatVisionService` was run against the exact Phase 0 inputs: two distinct images held
in the local chat database and the generated 2x2 red PNG. Persisted images remained in memory and
were not written to the repository. Ollama was restarted and confirmed healthy before each sample
to eliminate abandoned-request backlog.

Configuration was `qwen3-vl:4b`, the structured `VisionResult` format, temperature 0, context 8192,
and the current 180-second chat-vision timeout. Machine-readable results are stored in
`vision-baseline-qwen3-vl-4b-structured.json`.

## Before and after

| Sample | LLaVA Phase 0 | Qwen3-VL 4B candidate benchmark | Current structured pipeline rerun |
|---|---|---|---|
| Food image | Generic bread/buns; missed idli | Correctly identified six idlis | Timed out at 180.183 s |
| Aquarium image | Contradictory clownfish/Dory identification | Correctly identified clownfish without contradiction | Timed out at 180.398 s |
| Solid red fixture | Cautious; no object identified | Cautious; no object identified | Completed in 174.581 s; classified `other` with no invented item |

Completion rate for the current rerun was **1/3 (33.3%)**, compared with **3/3 (100%)** for the
Phase 0 LLaVA run and the earlier Qwen3-VL 4B candidate benchmark. The completed red-fixture result
was conservative, but no current-pipeline food or fish response reached the application.

## Conclusion

The earlier controlled candidate benchmark provides evidence that Qwen3-VL 4B is more accurate on
the food image and avoids LLaVA's fish contradiction. However, Phase 10 does **not** confirm an
end-to-end improvement under the current production configuration because two of three requests
timed out. Accuracy and conservatism cannot be scored for absent responses.

Phase 11 must tune residency/timeout behavior using the measured latency and improve degradation
messaging: the current timeout path says to pull the model even though it is already installed. The
Phase 10 rerun should then be repeated for the two timed-out samples before claiming deployable
before/after improvement.
