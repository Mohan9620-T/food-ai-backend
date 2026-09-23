# Web reading and image-based plans

## Enable web access

Set `ENABLE_WEB_SEARCH=true` in the backend environment. Add `TAVILY_API_KEY`
through the deployment's secret variables to enable broad live searches.
Do not commit API keys. Restart/redeploy after changing the environment.

Users can turn on **Search web** in the chat composer, ask to search online,
or paste an HTTP(S) URL. Explicit website requests bypass old uploaded-document
and image context. Replies include Markdown source links and are saved in chat history.

Public URL reading does not require Tavily. GitHub repository URLs retrieve public
metadata, an available README excerpt, and root filenames. This is not a full code
audit. A specific source-file URL can be supplied for a more focused question.
Private repositories, authenticated pages, JavaScript-only pages and inaccessible
sites are reported as unavailable; the application does not claim to have read them.

Reads are anonymous, bounded to two supplied URLs, one MB per response and 18,000
characters per page. DNS addresses are checked, connections are pinned to a validated
public IP, and redirects are rechecked. Private networks and nonstandard ports are
blocked. Retrieved text is untrusted evidence, never an instruction source.

Search uses [Tavily's Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search).
GitHub summaries use the [public repository contents API](https://docs.github.com/en/rest/repos/contents).
Explicit search failure or a missing key produces an honest message instead of a
made-up current answer. Existing general chat continues to work without a search key.

Non-streaming NVIDIA replies use `NVIDIA_CHAT_COMPLETE_TIMEOUT_SECONDS=120` to
allow a detailed answer to finish. Streaming keeps its separate 30-second idle
timeout between chunks (`NVIDIA_CHAT_TIMEOUT_SECONDS`).

## Image diet and workout requests

For requests such as “Use this BMI image to give me a seven-day diet and workout
plan,” vision first extracts visible measurements, labels and units. The normal
text model then writes the full requested plan using its streaming and automatic
continuation path. This avoids squeezing a multi-day plan into a small vision JSON
response and avoids the non-streaming first-response timeout for a long plan.

`NVIDIA_VISION_MAX_TOKENS=2048` is the recommended extraction budget. Existing
environments set to 384 need updating. Hosted image inference and its local fallback
share `NVIDIA_VISION_TIMEOUT_SECONDS`, so a failed hosted response cannot silently
start a long, independent local wait. Meal-photo logging retains its separate flow.

BMI and body-fat percentage are kept distinct. A reading alone does not establish
age, sex, health conditions, goals or individual calorie requirements. The assistant
provides a labeled general starter plan when details are missing, then asks for the
few details needed to personalize it. The plan and uploaded image remain in the
user's chat history.
