# Qwen3-VL 4B difficult-image results

Date: 2026-09-03

Model: `qwen3-vl:4b`

Ollama: Docker container, CPU inference

Request settings: structured `VisionResult` schema, temperature `0`, context `8192`

## Scope

The run selected one representative image for each requested difficult condition. The existing
multi-item thali supplies overlapping objects; deterministic dataset variants supply low light,
foreground occlusion, unusual angle, and tight crop. Raw machine-readable records are preserved in
`raw-results-qwen3-vl-4b.json`.

| ID | Condition | Expected | Limit | Outcome |
|---|---|---|---:|---|
| `indian-007` | Multiple overlapping thali items | Indian thali | 900 s diagnostic | Timed out; no response |
| `indian-010` | Low-light idli and sambar | idli, sambar | 180 s | Timed out; no response |
| `indian-012` | Occluded masala dosa and vada | masala dosa, vada | 180 s | Timed out; no response |
| `indian-013` | Rotated masala dosa | masala dosa | 180 s | Completed in 154.293 s after inference resizing; identified dosa, sambar, and chutney |
| `indian-015` | Tightly cropped vegetable biryani | vegetable biryani | 180 s | Timed out; no response |

The first two wall-clock measurements crossed a workspace suspension and are intentionally recorded
as `null`; their configured socket limits and timeout outcomes remain valid. The other three elapsed
measurements were approximately 180 seconds.

## Isolated-runtime diagnosis

`indian-013` was repeated after restarting Ollama and waiting for its health check, eliminating
queued work as the cause. It still timed out at 180 seconds. Ollama logs show:

- model initialization: about 45 seconds;
- image decoding: about 56.7 seconds across two batches;
- generation: about 4.64 tokens/second, with 100 tokens generated before cancellation;
- request termination: HTTP 500 at exactly 3 minutes after the client closed;
- post-run model memory: 5.352 GiB of the 7.518 GiB container limit (71.19%).

## Failure patterns

1. **Production timeout is the dominant failure.** None of the five difficult cases returned a
   response within the configured 180-second application limit. Accuracy cannot be scored when no
   structured result reaches the application.
2. **Cold-start and image encoding consume most of the latency budget.** The isolated run spent
   roughly 102 seconds loading the model and decoding the image before slow token generation.
3. **Timed-out requests can amplify latency for later requests.** In the sequential run, abandoned
   inference continued long enough to queue following work. The application's one-at-a-time vision
   slot prevents concurrent memory pressure but cannot cancel server-side work immediately.
4. **Inference resizing resolves the isolated rotated case.** After Phase 11, the production
   preprocessing path reduced the image before inference. The same case completed within its
   180-second diagnostic limit and returned the expected dish family plus acceptable context.
5. **No hallucination or label-error conclusion is possible from this run.** Every raw response is
   null. Treating a timeout as an incorrect food label would blur accuracy and availability metrics.

## Implications

Completion rate must be reported separately from label accuracy. Fine-tuning remains unindicated:
the rerun demonstrates recognition on the isolated difficult case, while inference throughput and
request lifecycle behavior on this host remain the measured constraints.
