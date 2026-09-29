# Image generation in chat

Send `Create an image of a robot chef` or select **Create image** and enter a description.
The composer clears immediately, shows a generation indicator, and returns an inline image
with View and Download actions. Images and their prompts are stored in the conversation;
refreshing restores them. Downloads require the owning user's login.

## Provider

Text-to-image defaults to NVIDIA's `black-forest-labs/flux.1-dev` endpoint, separately from
the Nemotron text and vision models. The existing server-side `NVIDIA_API_KEY` is used.
Never put the key in Angular environment files or source control.

- `ENABLE_IMAGE_GENERATION=true` (default): enables creation when the NVIDIA key is present.
- `NVIDIA_IMAGE_MODEL=black-forest-labs/flux.1-dev` (default): quality profile, 50 steps,
  guidance 3.5, one image. The optional `black-forest-labs/flux.2-klein-4b` profile retains
  the fast four-step endpoint. Unknown models are rejected before sending credentials.
- `NVIDIA_IMAGE_TIMEOUT_SECONDS=120` (default): provider read timeout.
- The app accepts up to **20,000 prompt characters**
  in both the composer and API, including Unicode characters.
- FLUX.1-dev accepts descriptions up to **10,000 characters**. The fast Klein hosted trial
  enforces **800 characters**, despite its reference page advertising 10,000. Requests
  exceeding the selected model's limit are prepared
  using the existing NVIDIA chat model before image generation. The preparation instruction
  preserves subjects, counts, anatomy, positioning, colors, setting and exclusions while
  removing repeated wording. It does not slice off the end of a prompt. Model-based
  compression can still omit details; inspect the resulting image for complex requests.
- Explicit photorealistic/live-action requests receive photography and composition guidance:
  follow the requested visible subject count and keep a viewpoint photographer off-camera
  unless explicitly requested in-frame. The complete description remains intact when it fits.
  Ordinary illustration prompts retain their requested medium. Character features are not guessed.
- The original request is kept in chat and attachment metadata. The exact provider prompt,
  compaction status, model, seed, steps, guidance, preparation version, resolved aspect ratio
  and actual pixel dimensions are saved with the attachment. Prompts and raw provider errors
  are not logged. No database migration is needed; existing generation metadata is used.
- A stable, nonzero seed is derived from the original request, model and aspect ratio. Zero
  previously requested a random result on every call. `/chat/images` accepts an optional
  integer `seed` from 1 through 2147483647 to request a different variation. Identical model
  inputs improve repeatability; provider updates/hardware and compression of exceptionally
  long prompts can still change results. Seed control does not guarantee anatomical accuracy.
- Prompts within the selected limit require no extra text-model call. Preparation uses JSON
  schema without a string `maxLength`: constrained decoding previously terminated strings
  mid-word while still returning valid JSON with `finish_reason=stop`. Local validation checks
  length and a completed sentence ending instead; invalid output is corrected before generation.
  It has a 90-second total deadline, a 40-second timeout per text-model call, and at most
  three attempts to produce valid, complete output. Oversized, malformed or incomplete
  descriptions are retried using the full original request. Incomplete responses receive
  a larger output budget up to 4,096 tokens (starting at 1,024 for the fast profile).
  Each text call retries a temporary connection failure or HTTP 502/503/504 once.
  Authorization, quota, explicit content rejections and `cannot_fit` are not retried.
  Quota errors return HTTP 429 with a specific retry-later message.
  Temporary failures preserve the entire draft and report a preparation error rather than
  incorrectly asking the user to stay under 800 characters. Invalid, truncated or refused
  preparation output never reaches the image endpoint.
- Preparation logs record character counts, attempt number and failure category, without
  recording prompt contents, credentials or raw provider responses. Image generation itself
  still runs once after preparation succeeds.
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
API references:
- https://docs.api.nvidia.com/nim/reference/black-forest-labs-flux_1-dev-infer
- https://docs.api.nvidia.com/nim/reference/black-forest-labs-flux_2-klein-4b-infer

This is text-to-image generation, not a character reference or identity-preservation system.
Names alone do not reliably reproduce a fictional character's hairstyle, costume or accessories.
Specify those visible attributes directly. Exact likeness needs a reference-capable provider;
this integration still cannot use arbitrary uploaded images as generation references. No model
or seed setting guarantees perfect anatomy, exact counts or identical output forever.

## People, comparisons and follow-up questions

Requests for a CM or other office-holder are resolved using current source evidence.
By default, the app returns that source and explains the portrait limitation without generating
an inaccurate face. Explicit requests for an artistic illustration, cartoon, painting or sketch
can still generate an image, labeled with the intended subject and an unverified likeness.
This hosted integration cannot use an uploaded portrait as a generation reference, so it does
not promise accurate real-person likenesses. The provider's preview editing API only accepts
its predefined example images.

Generated images remain available as visual evidence after reload, alongside uploaded images.
"Who is he?" about a generated image uses the saved request and source, not a face identification
or a generic web search. Legacy images without a saved subject report the original request
without inventing a name. Unrelated conversation clears implicit references; an explicit
"the generated image" reference can select it again. Following a multi-image upload, an
ambiguous identity question asks which image the user means.

Uploaded comparisons receive recent conversation and relevant generation provenance. Exact
file copies can be associated with their saved generation request; this is file comparison,
not facial recognition. The response separates intended subject, visible differences, and
the generator's limitation. It must not invent captions, identify people from faces or decide
whether two photographs show the same person. Only the user's owned session is consulted.
Generated-image comparisons first inspect every supplied image, then use the text model to
explain those observations alongside the saved intended subject. This second step does not
search the web, has a 45-second deadline, and retains the visual observations if unavailable.

NVIDIA's hosted access is a trial service, not a promise of unlimited free production use.
Google's Gemini 3 Pro Image API has no free tier; switching to it requires a separate billed
Google API account and integration. It is not enabled by this fix. Free model weights also
require GPU hosting, which has its own operating cost. Pricing reference:
https://ai.google.dev/gemini-api/docs/pricing#gemini-3-pro-image

## Image follow-up reliability

Editing commands about an attached/referenced image (for example, "enhance the cinematic
atmosphere" or "make it brighter") receive an explanation of the text-only generator's
capability, without calling image analysis or claiming the file was edited. A new generated
version cannot guarantee the original face, pose or composition.

NVIDIA image analysis retries incomplete/malformed output once using the same images and
remaining request deadline, increasing the output budget up to 8192 tokens. Provider refusals
are not retried or passed to another provider. Production chat and vision use Ollama fallback
only when `ENABLE_OLLAMA_FALLBACK=true` is explicitly set for a provisioned model server;
the default remains enabled in development. This is separate from readiness configuration.

Failure responses retain partial output and offer a retry without exposing model setup
instructions. The UI also normalizes those older failure notices when restoring history.
Unexpected stream failures send a terminal error event so the composer does not stay busy.
Provider availability still depends on network access, capacity and account quota.

## Regression checks

`tests/test_image_generation.py` covers provider responses, validation, failures, history,
authentication and cross-user download isolation. Frontend tests cover intent routing,
composer cleanup/retry, private preview loading and the browser generation/history flow.
The long cinematic prompt fixture covers recovery from oversized, malformed, empty and
token-limited preparation responses while retaining the original request on every attempt.
A live provider check also generated a valid 1024-by-1024 PNG from the 3,273-character fixture.

`tests/test_image_quality.py` adds full-prompt preservation, repeatable request settings,
model-specific parameter limits, explicit seed validation, metadata/history persistence,
photography intent and incomplete-but-valid JSON regressions. The quality endpoint was also
tested live with the reported 1,826-character description at 768 by 1344 pixels. The complete
description reached the image endpoint, including its final framing requirements.
Two identical live robot-scene requests produced identical PNG bytes with the same nonzero
seed. A separate character-scene output was filtered by the provider and was not retried;
the model switch does not override the provider's content decisions. Visual inspection
found improved single-subject composition, but did not establish an exact character likeness.
