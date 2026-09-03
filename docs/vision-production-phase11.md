# Phase 11 production vision configuration

Date: 2026-09-03

## Final settings

| Setting | Value | Reason |
|---|---:|---|
| `OLLAMA_KEEP_ALIVE` | `1h` | Avoid repeated ~45-second model loads during an active session. |
| `OLLAMA_VISION_TIMEOUT_SECONDS` | `660` | Bound meal-image work while allowing the measured 580.674-second baseline request plus headroom. |
| `OLLAMA_CHAT_VISION_TIMEOUT_SECONDS` | `660` | Use the same bound for general image chat. |
| `num_ctx` | `8192` | Preserve support for the 4,234-token Phase 0 image. |
| `num_predict` | `1024` | Bound slow generation without truncating the model's structured response. |
| `think` | `false` | Reduce unnecessary reasoning latency for schema-constrained extraction. |

The 660-second deadline is an upper operational boundary, not a promise that every accepted image
will complete. Increasing it beyond 11 minutes would produce unacceptable user waits on this host.

## Qwen3-VL Ollama compatibility

Live testing showed that Qwen3-VL 4B can return a complete schema-conforming result in
`message.thinking` while leaving `message.content` empty, even when `think` is false. Both vision
services now select non-empty `content` first, then `thinking`, and validate the selected value with
`VisionResult`. Arbitrary thinking text cannot reach users because schema validation is mandatory.

## Live verification

| Baseline sample | Phase 10 at 180 s | Tuned warm result |
|---|---|---|
| Solid red fixture | Success in 174.581 s | Success in 65.411 s; no invented food/object |
| Idli image | Timeout in 180.183 s | Success in 46.313 s using cached prompt; idli high confidence, sambar/chutney qualified as likely |
| Aquarium image | Timeout in 180.398 s | Graceful timeout in 660.500 s |

The warm red-fixture latency improved by 62.5%. The successful food result is materially more
specific than LLaVA's Phase 0 bread/buns description and more conservative because lower-confidence
condiments are explicitly qualified.

The large aquarium image still exceeded the upper bound. Ollama logs showed 4,056 visual tokens,
four image decode batches totaling about 274 seconds, and only 81 generated tokens by cancellation.
This establishes image preprocessing/downscaling as a recommended follow-up rather than a reason to
permit still longer requests.

## Graceful degradation

- A request timeout raises `VisionModelUnavailableError` with an accurate retry message and the
  configured deadline; it no longer incorrectly tells the user to pull an installed model.
- Connection and HTTP failures produce a separate message asking the operator to confirm Ollama is
  running and the configured model is installed.
- Invalid or truncated structured output returns the existing safe fallback rather than crashing.
- API routes retain their existing 503 mapping for model availability failures.

Focused tests exercise timeout, connection failure, malformed output, Qwen's thinking-field result,
and shared inference-slot behavior.
