# Phase 12 fine-tuning decision

Date: 2026-09-03

## Decision

Do not fine-tune the vision model at this stage.

Phase 12 was authorized after the image-speed work, but the plan's prerequisite for training is
not met: there is no repeatable evidence that recognition quality is insufficient. The earlier
difficult-image failures were timeouts with no model answer, not incorrect labels. After aligning
the evaluator with production preprocessing, the isolated rotated-dosa case completed in 154.293
seconds and identified dosa, sambar, and chutney. Its expected label is masala dosa, with sambar
and chutney explicitly accepted as context.

## Why training would be the wrong fix

- Fine-tuning changes model behavior; it does not materially accelerate CPU inference.
- The repository contains an 18-file evaluation set, not an independent training corpus.
- Nine files are deterministic variants of nine source photographs, so treating all 18 as
  independent training examples would overstate dataset size.
- Training on these files would contaminate the fixed holdout set and make later accuracy claims
  unreliable.
- This workstation's demonstrated constraint is Ollama runtime throughput and memory, including
  `unexpected EOF` when two vision runners were resident under the Docker memory limit.

## Evidence reviewed

- Qwen3-VL 4B correctly recognized the regional food sample where LLaVA used generic bread/buns.
- The optimized 4B aquarium request remained correct and became 76.3% faster.
- Qwen3-VL 2B was faster in its best warm run but introduced duplicated food items and a false
  object on the solid-red control, so it was not promoted.
- The production-aligned rotated difficult case returned the expected dish family and acceptable
  accompaniments.

## Revisit criteria

Fine-tuning should be reconsidered only after collecting a separate, consented, representative
training corpus and demonstrating repeatable label failures on the untouched evaluation set.
Record per-label precision/recall and hallucination rates first. For the present speed goal, GPU
acceleration or a better-performing quantized runtime/model is the appropriate next investigation.
