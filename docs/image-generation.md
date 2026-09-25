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
- One image per request; four inference steps; maximum 10,000 prompt characters.
- Square by default; portrait/9:16 and landscape/16:9 descriptions select the provider's
  supported portrait/landscape dimensions. `/chat/images` also accepts `aspect_ratio`.
- Five requests per minute per the application's rate limiter.
- Provider failures/refusals produce an explicit error, never a fake image or text-model
  success. Requests are not automatically repeated, avoiding duplicate generation charges.
- Editing uploaded images is not supported by this integration; the composer explains this
  instead of silently ignoring the reference image. Existing image analysis still works.

The provider's hosted API terms, quotas and content restrictions apply.
API reference: https://docs.api.nvidia.com/nim/reference/black-forest-labs-flux_2-klein-4b-infer

## Validation

`tests/test_image_generation.py` covers provider responses, validation, failures, history,
authentication and cross-user download isolation. Frontend tests cover intent routing,
composer cleanup/retry, private preview loading and the browser generation/history flow.
