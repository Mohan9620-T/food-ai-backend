# Vision inference speed optimization

Captured on 2026-09-03 after Phase 11.

## Production change

Images whose longest edge exceeds 1024 pixels are orientation-corrected and resized to a
1024-pixel JPEG inference copy before they are sent to Ollama. The original uploaded bytes are
still persisted and returned in chat history, so this does not reduce stored-image quality.
Normal-sized images are sent unchanged. The limit is configurable with
`OLLAMA_VISION_MAX_DIMENSION`.

Both general image chat and meal-image parsing use the same preprocessing path.

## Large-image benchmark

The persisted aquarium sample is a 6000 x 4000 JPEG (24 megapixels). Previously, Qwen3-VL 4B
took 580.674 seconds in the Phase 2 comparison and later exceeded the 660-second production
timeout. With the 1024 x 683 inference copy it completed in 137.490 seconds and correctly
identified the clownfish. This is 76.3% faster than the comparable completed Phase 2 run and
eliminates the later timeout.

Raw output for the optimized 4B measurement is in
[`vision-speed-optimization.json`](vision-speed-optimization.json).

## Smaller-model experiment

Qwen3-VL 2B was evaluated but not selected for production. It achieved a 9.474-second warm best
case on the aquarium, but took 108.909 seconds on the idli image, repeated items in its structured
result, and treated a solid-red control as a visible object. Those regressions conflict with the
project's recognition-quality requirement. Qwen3-VL 4B therefore remains the configured model.

## Remaining constraint

The application-side large-image bottleneck is fixed, but first-request and CPU-only model
inference latency remain hardware/runtime constraints. Keeping the model resident avoids repeated
load time. GPU acceleration would be the next step for consistently low first-response latency;
fine-tuning is not a speed optimization.
