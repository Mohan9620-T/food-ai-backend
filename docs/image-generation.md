# Image generation in chat

Send `Create an image of a robot chef` or select **Create image** and enter a description.
The composer clears immediately, shows a generation indicator, and returns an inline image
with View and Download actions. Images and their prompts are stored in the conversation;
refreshing restores them. Downloads require the owning user's login.

## Provider

Text-to-image uses NVIDIA's `black-forest-labs/flux.2-klein-4b` endpoint, separately from
the Nemotron text and vision models. The existing server-side `NVIDIA_API_KEY` is used.
Never put the key in Angular environment files or source control.

- `ENABLE_IMAGE_GENERATION=true` (default): enables creation when the NVIDIA key is present.
- `NVIDIA_IMAGE_TIMEOUT_SECONDS=120` (default): provider read timeout.
- One image per request; four inference steps; the app accepts up to **20,000 prompt characters**
  in both the composer and API, including Unicode characters.
- The NVIDIA hosted endpoint currently enforces **800 characters** (verified against the
  live endpoint; its reference page currently says 10,000). Longer requests are prepared
  using the existing NVIDIA chat model before image generation. The preparation instruction
  preserves subjects, counts, anatomy, positioning, colors, setting and exclusions while
  removing repeated wording. It does not slice off the end of a prompt. Model-based
  compression can still omit details; inspect the resulting image for complex requests.
- The original request is kept in chat and attachment metadata. The exact provider prompt,
  whether it was compacted, the resolved aspect ratio and actual pixel dimensions are also
  saved with the attachment. Prompts and raw provider errors are not logged.
- Short prompts require no extra text-model call. Preparation has a 90-second total deadline,
  a 40-second timeout per text-model call, and at most one correction if the prepared
  description is still too long. Each text call retries a temporary connection failure or
  HTTP 502/503/504 once. Authorization, quota and content rejections are not retried.
  Temporary failures preserve the entire draft and report a preparation error rather than
  incorrectly asking the user to stay under 800 characters. Invalid, truncated or refused
  preparation output never reaches the image endpoint.
- Square by default; portrait/9:16 and landscape/16:9 descriptions select the provider's
  supported portrait/landscape dimensions. `/chat/images` also accepts `aspect_ratio`.
- Five requests per minute per the application's rate limiter.
- Provider failures/refusals produce an explicit error, never a fake image or text-model
  success. Prompt-length and image-setting validation errors are distinguished from explicit
  content refusals. Image requests are not automatically repeated or routed to another
  provider after refusal, avoiding duplicate generation charges.
- Editing uploaded images is not supported by this integration; the composer explains this
  instead of silently ignoring the reference image. Existing image analysis still works.

The provider's hosted API terms, quotas and content restrictions apply.
API reference: https://docs.api.nvidia.com/nim/reference/black-forest-labs-flux_2-klein-4b-infer

NVIDIA's hosted access is a trial service, not a promise of unlimited free production use.
Google's Gemini 3 Pro Image API has no free tier; switching to it requires a separate billed
Google API account and integration. It is not enabled by this fix. Free model weights also
require GPU hosting, which has its own operating cost. Pricing reference:
https://ai.google.dev/gemini-api/docs/pricing#gemini-3-pro-image

## Validation

`tests/test_image_generation.py` covers provider responses, validation, failures, history,
authentication and cross-user download isolation. Frontend tests cover intent routing,
composer cleanup/retry, private preview loading and the browser generation/history flow.
