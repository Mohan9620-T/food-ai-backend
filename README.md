# Food AI Backend

A full-stack food & nutrition assistant with a FastAPI backend, Angular frontend, JWT authentication, and a local Ollama-powered AI chat.

## Quickstart with Docker

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) with Docker Compose.
- Windows 11 users: WSL2 enabled and integrated with Docker Desktop.
- Git for cloning the repository.

Clone the repository and enter it:

```powershell
git clone https://github.com/Mohan9620-T/food-ai-backend.git
cd food-ai-backend
```

Create the runtime configuration from the sanitized template:

```powershell
Copy-Item .env.example .env
```

Open `.env` and replace at least `DB_PASSWORD` and `JWT_SECRET_KEY` with strong,
non-empty values. Never commit `.env`.

Build and start the FastAPI backend, PostgreSQL, and Ollama services:

```powershell
docker compose up --build
```

In a second terminal, pull the configured vision model into Ollama's persistent volume:

```powershell
docker compose exec ollama ollama pull qwen3-vl:4b
```

Verify the stack:

```powershell
docker compose ps
curl.exe --fail http://localhost:8000/health
```

The health response should be `{"status":"Healthy"}`. Swagger UI is available at
<http://localhost:8000/docs>. The Angular frontend is run separately; see
[Frontend setup](#frontend-setup).

### Common Windows setup issues

- **PowerShell blocks script execution:** for the current terminal only, run
  `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned`, then activate the
  virtual environment again. Docker-only setup does not require Python activation.
- **WSL2 is not enabled:** open PowerShell as Administrator, run `wsl --install` and
  `wsl --update`, then reboot Windows before reopening Docker Desktop.
- **Docker reports “virtualization support not detected”:** enable Intel VT-x or AMD-V
  in BIOS/UEFI, enable the Windows features **Virtual Machine Platform** and
  **Windows Subsystem for Linux**, and reboot. Confirm virtualization shows as enabled
  in Task Manager before starting Docker Desktop again.

## Environment variables

Copy `.env.example` to `.env`, then adjust the values for your environment. “Required”
means the value must be supplied for the normal local/Compose setup; optional settings
use the documented default or disable the associated integration when empty.

| Variable | Requirement | Purpose / default |
| --- | --- | --- |
| `DB_HOST` | Required | PostgreSQL hostname; use `127.0.0.1` for local Uvicorn and `postgres` inside Compose. The local Compose port is bound to IPv4 only. |
| `DB_PORT` | Required | PostgreSQL port; normally `5432`. |
| `DB_NAME` | Required | PostgreSQL database name. |
| `DB_USER` | Required | PostgreSQL user name. |
| `DB_PASSWORD` | Required | PostgreSQL password; must be non-empty for Compose. |
| `ENABLE_WORKSPACES` | Optional | Default `false`. Enable workspace dashboards, owner-scoped documents, conversation scope, search and exports after migration `0022_workspaces`. |
| `ENABLE_EMAIL_OTP` | Optional | `true`; set `false` for password-only signup/login in any environment. OTP and email-based reset endpoints become unavailable. |
| `ENABLE_EMAIL_DELIVERY` | Optional | `true`; set `false` to prevent all outbound emails and welcome password-link creation. Also disables OTP requirements. |
| `ENABLE_CODE_ASSISTANT` | Optional | `false`; enable owner-scoped repository inspection after migration `0025_code_assistant`. |
| `CODE_REPOSITORY_ROOT` | Optional | Empty; operator-provisioned base directory, with a separate `<user-id>/<repository>` folder for each account. Required when connecting repositories. |
| `CODE_MAX_FILES` | Optional | `500`; maximum indexed UTF-8 source files per repository, clamped to 1–500. |
| `CODE_AGENT_MAX_STEPS` | Optional | `6`; maximum tool/planner steps per question, clamped to 1–8. |
| `CODE_AGENT_TIMEOUT_SECONDS` | Optional | `120`; total agent deadline, clamped to 1–300 seconds. |
| `ENABLE_OAUTH_LOGIN` | Optional | `false`; verified Firebase Google/Microsoft login. |
| `OAUTH_FIREBASE_PROJECT_ID` | Optional | Firebase project ID used to verify token audience and issuer. |
| `OAUTH_FIREBASE_API_KEY` | Optional | Public Firebase Web API key, returned to the sign-in client only when enabled. |
| `OAUTH_FIREBASE_AUTH_DOMAIN` | Optional | Authorized Firebase Auth domain. |
| `ENABLE_API_KEYS` | Optional | `false`; hashed personal API keys and the settings page. |
| `ENABLE_ROLES` | Optional | `false`; role enforcement and administrator diagnostics. |
| `ENABLE_LLM_GATEWAY` | Optional | `false`; pooled NVIDIA client with bounded concurrency and retries. |
| `LLM_MAX_CONCURRENCY` | Optional | `8`; maximum NVIDIA requests in flight. |
| `LLM_MAX_RETRIES` | Optional | `2`; jittered retry limit for transport failures and retryable statuses. |
| `ENABLE_REQUEST_IDS` | Optional | `false`; X-Request-ID response headers, error bodies and log context. |
| `OTEL_ENABLED` | Optional | `false`; FastAPI/httpx/SQLAlchemy tracing when optional instrumentation is installed. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Optional | `http://localhost:4317`; tracing collector. |
| `ENABLE_INVOICE_EXTRACTION` | Optional | `false`; enable the authenticated, stateless invoice extraction API. |
| `INVOICE_VLM_PROVIDER` | Optional | `ollama`; select `ollama` or `nvidia` explicitly, using existing provider URLs/credentials. |
| `INVOICE_VLM_MODEL` | Optional | Empty; uses the selected provider's existing chat vision model. |
| `INVOICE_MAX_PAGES` | Optional | `5`; reject PDFs above this cap before inference (bounded to 1-10). |
| `INVOICE_MAX_UPLOAD_BYTES` | Optional | `15728640`; upload limit, bounded to at most 15 MiB. |
| `INVOICE_VLM_TIMEOUT_SECONDS` | Optional | `120`; total preparation, inference and repair deadline. |
| `INVOICE_EXTRACTION_RATE_LIMIT` | Optional | `5/minute`; per-client extraction limit. |
| `DATABASE_URL` | Optional | Full SQLAlchemy connection URL; when empty, the `DB_*` values are used. |
| `JWT_SECRET_KEY` | Required | Long random secret used to sign access tokens; must be non-empty for Compose. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Optional | Standard access-token lifetime; default `30`. |
| `REFRESH_TOKEN_EXPIRE_DAYS` | Optional | Refresh-token lifetime; default `7`. |
| `REMEMBERED_ACCESS_TOKEN_EXPIRE_DAYS` | Optional | Remember-me access-token lifetime; default `30`. |
| `SMTP_HOST` | Optional | SMTP server hostname; leaving it empty disables account email delivery. |
| `SMTP_PORT` | Optional | SMTP server port; default `587`. |
| `SMTP_USERNAME` | Optional | SMTP account user name. |
| `SMTP_PASSWORD` | Optional | SMTP password or provider app password. |
| `SMTP_FROM_EMAIL` | Optional | Sender address; defaults to `SMTP_USERNAME` when empty. |
| `SMTP_USE_TLS` | Optional | Enables SMTP STARTTLS; default `true`. |
| `OLLAMA_URL` | Optional | Ollama chat endpoint; host-run default `http://localhost:11434/api/chat` (Compose overrides it). |
| `OLLAMA_MODEL` | Optional | Text-chat model; default `qwen3:8b`. |
| `OLLAMA_TIMEOUT_SECONDS` | Optional | Text-model request timeout; default `300`. |
| `OLLAMA_KEEP_ALIVE` | Optional | How long Ollama keeps a loaded model resident; default `1h`. |
| `OLLAMA_CHAT_THINK` | Optional | Enables Qwen reasoning mode; default `false` for faster chat. |
| `OLLAMA_CHAT_MAX_TOKENS` | Optional | Output tokens per local text-chat request; default `2048` so the fallback can give substantive explanations. |
| `OLLAMA_VISION_MODEL` | Optional | Meal-image vision model; default `qwen3-vl:4b`. |
| `OLLAMA_VISION_TIMEOUT_SECONDS` | Optional | Meal-image request timeout; default `660`. |
| `OLLAMA_VISION_MAX_DIMENSION` | Optional | Longest image edge sent to Ollama; default `1024`. Stored originals are unchanged. |
| `OLLAMA_CHAT_VISION_MODEL` | Optional | General image-chat model; default `qwen3-vl:4b`. |
| `APP_ENVIRONMENT` | Optional | Set to `production` to use NVIDIA regardless of `LLM_PROVIDER`; default `development`. |
| `ENABLE_OLLAMA_FALLBACK` | Optional | Chat and vision fallback to Ollama. Unset: enabled in development, disabled in production. Set `true` only with a reachable Ollama server and installed models. Independent of readiness checks. |
| `LLM_PROVIDER` | Optional | Development override: `ollama` is local-only; `nvidia` uses NVIDIA first with one Ollama fallback. |
| `LLM_PROFILE_OVERRIDES` | Optional | JSON object keyed by chat-mode profile name (`general`, `reasoning`, `math`, `coding`, `planning`, `document`), patching `model`/`thinking`/`temperature`/`top_p`/`max_tokens`/`timeout_seconds`/`label`/`description` per profile; malformed JSON or unknown fields are logged and ignored rather than failing the request. |
| `ENABLE_TURN_GOVERNANCE` | Optional | Deterministic, no-LLM-call pre-answer gate (`app/services/turn_governance.py`) that can block, escalate, or ask for clarification before generation runs; default `false`. |
| `WEB_SEARCH_DAILY_CAP` | Optional | Global per-day cap on web-search provider calls (not direct link reads); default `80`. Redis-backed (`ENABLE_REDIS_CACHE`); fails open (search still allowed) when Redis is unreachable. |
| `WEB_SEARCH_IMAGE_PREVIEW_TIMEOUT_SECONDS` | Optional | HTTP client timeout for vetting web-search preview images; default `6`. |
| `ENABLE_WEB_SEARCH_SOURCE_CARDS` | Optional | Structured source persistence, the `status`/`sources` NDJSON stream events, preview-image vetting, and the frontend source-card UI; default `false`. Off: web search behaves exactly as before (markdown "### Sources" footer only). The daily cap and explicit-content filter above are hardening of web search itself and always apply once `ENABLE_WEB_SEARCH` is on, independent of this flag. |
| `NVIDIA_API_KEY` | Required for NVIDIA | NVIDIA API credential. Never commit a real value. |
| `NVIDIA_API_BASE_URL` | Optional | NVIDIA OpenAI-compatible API base URL. |
| `NVIDIA_CHAT_MODEL` | Optional | NVIDIA text model; defaults to `nvidia/nemotron-3-super-120b-a12b`. |
| `NVIDIA_REASONING_MODEL` | Optional | NVIDIA model used by the `reasoning`/`math` chat-mode profiles; falls back to `NVIDIA_CHAT_MODEL` when empty. |
| `NVIDIA_CHAT_CONNECT_TIMEOUT_SECONDS` | Optional | NVIDIA text connection timeout; default `5`. |
| `NVIDIA_CHAT_TIMEOUT_SECONDS` | Optional | NVIDIA text response-read timeout; default `30`. |
| `DOCUMENT_AI_TIMEOUT_SECONDS` | Optional | Total deadline for each document AI operation, including provider fallback and all tokens; default `90`. Keep this greater than the NVIDIA timeout so the one Ollama fallback can run. Does not affect ordinary chat or direct text export. |
| `DOCUMENT_AI_MAX_TOKENS` | Optional | Output budget for document Q&A and planning; default `2048`. Ordinary chat uses its independent provider budgets. |
| `DOCUMENT_GENERATION_MAX_TOKENS` | Optional | Output budget for complete generated document structures; default `8192`, capped at `16384`. An invalid structure gets at most one correction attempt, with a separate document AI deadline. |
| `DOCUMENT_PLAN_CONFIDENCE_THRESHOLD` | Optional | Minimum confidence accepted for semantic document plans before the assistant asks for clarification; default `0.65`. |
| `DOCUMENT_PIPELINE_MAX_STEPS` | Optional | Maximum validated operations in one natural-language document pipeline; default `8`, range `1`–`20`. |
| `DOCUMENT_CONVERSION_TIMEOUT_SECONDS` | Optional | Headless LibreOffice conversion deadline in seconds; default `90`. |
| `DOCUMENT_OOXML_MAX_UNCOMPRESSED_BYTES` | Optional | Absolute pre-parse decompressed-size ceiling for DOCX/XLSX/PPTX; default `134217728` (128 MiB). |
| `DOCUMENT_OOXML_MAX_TOTAL_RATIO` | Optional | Maximum total decompressed-to-upload size ratio for OOXML archives; default `100`. |
| `DOCUMENT_OOXML_MAX_ENTRY_RATIO` | Optional | Maximum decompressed-to-compressed size ratio for one OOXML entry; default `200`. |
| `DOCUMENT_OOXML_MAX_ENTRIES` | Optional | Maximum number of entries in an OOXML archive; default `5000`. |
| `NVIDIA_CHAT_MAX_TOKENS` | Optional | NVIDIA text-chat answer allowance per request; default `4096`, independent of Ollama's limit. When reasoning is enabled, its budget and a 500-token closing allowance are added to this limit. |
| `NVIDIA_CHAT_REASONING_BUDGET` | Optional | Opt-in reasoning for `nvidia/nemotron-3-super-120b-a12b` text chat; default `0` (disabled), range `0`-`8192`. Disabled by default after capped reasoning responses exposed unfinished deliberation in the provider's answer field. Other models and atomic document requests retain their settings. |
| `CHAT_MAX_CONTINUATIONS` | Optional | Extra requests to the same provider when a text response reaches its token limit; default `3`, range `0`–`8`. Text continues in the same message. A remaining interruption is reported honestly. Atomic document generation retains its separate token budget. |
| `NVIDIA_TEST_CHAT_MODEL` | Optional | NVIDIA model used only by `/nvidia-chat`; defaults to `nvidia/nemotron-3-ultra-550b-a55b`. |
| `NVIDIA_CHAT_VISION_MODEL` | Optional | NVIDIA image-chat model; defaults to `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`. |
| `NVIDIA_VISION_CONNECT_TIMEOUT_SECONDS` | Optional | NVIDIA vision connection timeout; default `5`. |
| `NVIDIA_VISION_TIMEOUT_SECONDS` | Optional | NVIDIA vision response-read timeout; default `45`. |
| `NVIDIA_VISION_MAX_DIMENSION` | Optional | Longest image edge sent to NVIDIA; default `768`. Stored originals are unchanged. |
| `NVIDIA_VISION_MAX_TOKENS` | Optional | Maximum NVIDIA response tokens; default `384`. |
| `CHAT_VISION_OCR_ENABLED` | Optional | Enables a second local Tesseract OCR pass; default `false` for lower latency. |
| `OLLAMA_CHAT_VISION_TIMEOUT_SECONDS` | Optional | General image-chat timeout; default `660`. |
| `USDA_API_KEY` | Optional | USDA FoodData Central key; nutrition matches are unavailable when empty. |
| `USDA_API_URL` | Optional | USDA API base URL; default `https://api.nal.usda.gov/fdc/v1`. |
| `USDA_TIMEOUT_SECONDS` | Optional | USDA request timeout; default `15`. |
| `ALLOWED_ORIGINS` | Optional | Comma-separated browser origins allowed by CORS; default `http://localhost:4200`. In `APP_ENVIRONMENT=development`, HTTP(S) origins on `localhost`, `127.0.0.1`, and `[::1]` are also allowed on any port. All other environments use only the configured origins. |
| `LOGIN_RATE_LIMIT` | Optional | Login limit in SlowAPI format; default `5/minute`. |
| `REGISTER_RATE_LIMIT` | Optional | Registration limit; default `5/minute`. |
| `MEAL_CREATE_RATE_LIMIT` | Optional | Meal-creation limit; default `10/minute`. |
| `CHAT_VISION_RATE_LIMIT` | Optional | Image-chat limit; default `2/minute`. |
| `MIGRATION_CHECK_ENABLED` | Optional | Enables the startup warning for pending Alembic migrations; default `true`. |
| `CONTEXT_TOKEN_BUDGET` | Optional | Approximate total token budget for one assembled chat request (system prompt, persistent user memory, rolling summary, document references, recent history); default `24000`. |
| `CONTEXT_TOKEN_CHAR_DIVISOR` | Optional | Characters-per-token approximation used only for budgeting, not a real tokenizer; default `4`. |
| `ENABLE_REDIS_CACHE` | Optional | Caches recently-assembled chat history in Redis; default `false`. PostgreSQL remains the source of truth - every read/write degrades to a no-op (falling back to PostgreSQL) when disabled or unreachable. See `REDIS_URL`/`REDIS_HOST`/`REDIS_PORT`/`REDIS_DB`/`REDIS_PASSWORD`/`REDIS_TIMEOUT_SECONDS`/`REDIS_CHAT_TTL_SECONDS`. |
| `ENABLE_SEMANTIC_RAG` | Optional | Adds ChromaDB-backed semantic chunk retrieval for uploaded documents, on top of (never instead of) the deterministic raw-text extraction path; default `false`. Falls back to that deterministic path when Chroma or the embedding provider is unreachable. See `CHROMA_HOST`/`CHROMA_PORT`/`CHROMA_COLLECTION_PREFIX`/`CHROMA_TIMEOUT_SECONDS`/`EMBEDDING_PROVIDER`/`EMBEDDING_MODEL`. |

### Chat response detail

Text chat gives substantive explanations by default, with context, examples, useful
headings, and relevant caveats. Broad topics typically receive 400-800 words when
there is enough useful material; this is guidance, not a required length. Comparisons
can include tables and practical tradeoffs. Greetings, simple facts, and explicit
requests for brief replies or strict formats remain short.

The same instructions reach NVIDIA and the Ollama fallback in the existing request;
there is no extra expansion call. Nemotron 3 Super uses bounded low-effort reasoning
with an additional token allowance to preserve room for the answer, following
[NVIDIA's model guidance](https://build.nvidia.com/nvidia/nemotron-3-super-120b-a12b/modelcard)
and [NIM budget controls](https://docs.nvidia.com/nim/large-language-models/1.15.0/thinking-budget-control.html).
Text chat retains automatic continuation when a
provider reaches its output limit. Changing response depth does not add web search:
the assistant must not invent sources or claim to have verified live information.

### YouTube channel video lists

With `ENABLE_WEB_SEARCH=true`, requests such as `Show the latest five videos from
https://www.youtube.com/@Example` read that channel's public Videos tab. Follow-ups
such as `show the latest video on the channel` use recent channel references from
the same saved conversation; an explicitly named new channel takes precedence.
Tavily can discover channel URLs from a channel name. Video search hits and snippets
are never treated as proof of channel ownership or the latest upload.

The backend checks the channel ID/handle, reads only upload cards in the selected
Videos tab, and verifies the Latest ordering. A verified public upload feed provides
a fallback when available. Results are numbered links with the publish-time labels
returned by YouTube and a link to the retrieved channel page/feed. These lists do
not need an LLM rewrite or a YouTube API key. Public pages/feeds can be unavailable;
unverified or ambiguous channels return a clear message without unrelated videos.
Lists are bounded to 30 recent entries per request, not a complete channel archive.

## Project structure

\`\`\`
food-ai-backend/
├── app/                    # FastAPI backend
│   ├── api/                 # Route handlers (users, chat)
│   ├── config/               # Settings loader (.env)
│   ├── database/             # SQLAlchemy engine/session
│   ├── models/                # ORM models
│   ├── repositories/          # DB access layer
│   ├── schemas/                # Pydantic request/response models
│   ├── services/                # Business logic (auth, chat)
│   ├── utils/                    # Security (JWT, password hashing)
│   └── main.py                    # App entrypoint
├── tests/                    # pytest test suite
└── requirements.txt
\`\`\`

The Angular frontend lives in its own sibling repository, `food-ai-ui/` (not a
subdirectory of this repo). It talks to this API purely over HTTP/CORS, so the
two can be developed, versioned, and deployed independently. See its own
README for setup; `ALLOWED_ORIGINS` below controls which frontend origins this
API accepts requests from.

For how the chat memory system, NVIDIA/Ollama provider routing, and the document
upload/retrieval pipeline fit together (with diagrams), see
[docs/architecture.md](docs/architecture.md). Deployment to Railway is covered in
[docs/railway-deployment.md](docs/railway-deployment.md) and
[docs/production-deployment.md](docs/production-deployment.md), not duplicated here.

## Prerequisites

- Python 3.13
- Docker Engine 24+ with Docker Compose v2.20+ (for the containerized setup)
- PostgreSQL running locally
- [Ollama](https://ollama.com) running locally with `qwen3:8b` and `qwen3-vl:4b` pulled
- Tesseract OCR (optional, for more accurate text recognition in chat images)

## Backend setup

1. Create and activate a virtual environment:
   \`\`\`
   python -m venv venv
   .\\venv\\Scripts\\Activate      # Windows
   source venv/bin/activate     # macOS/Linux
   \`\`\`

2. Install dependencies:
   \`\`\`
   pip install -r requirements.txt
   \`\`\`

3. Copy the complete root configuration template (never commit the resulting `.env`):
   ```powershell
   Copy-Item .env.example .env
   ```

   Replace `DB_PASSWORD` and `JWT_SECRET_KEY`, then review the
   [environment variable reference](#environment-variables) for optional integrations.

   SMTP settings are optional for local development. When configured, a newly
   registered user receives an email containing the submitted login credentials.

   Meal nutrition lookup requires a free USDA FoodData Central API key from
   [the USDA API key signup page](https://fdc.nal.usda.gov/api-key-signup.html).
   The API still starts without the key and logs a warning; meal items are then
   saved as unmatched with no calorie or macro values until a key is configured.

   Image meal logging also requires the configured Ollama vision model locally:
   \`\`\`
   ollama pull qwen3-vl:4b
   \`\`\`
   If it is unavailable, text meal logging and the rest of the application continue
   working; image uploads return a clear service-unavailable response.

   General chat image OCR uses the `pytesseract` Python package plus the separate
   Tesseract system executable. Install Tesseract and ensure its executable is on
   `PATH` (for example, install Tesseract OCR on Windows or `tesseract-ocr` through
   your Linux package manager). If the executable is missing, image chat continues
   with vision-only analysis and logs a warning; the application does not fail.

4. Start PostgreSQL and wait until it is ready. With Docker Desktop running:
   ```powershell
   docker compose up -d --wait --wait-timeout 60 postgres
   ```
   The Compose service creates the database named by `DB_NAME` on its first run
   and retains it in the existing `postgres_data` volume. For local Uvicorn, set
   `DB_HOST=127.0.0.1` and `DB_PORT=5432` in `.env`; `localhost` can resolve to
   IPv6, which this Compose port does not expose. If you use a separately managed
   PostgreSQL server, start that server and create the configured database there.

5. Apply all database migrations. This is required before starting the API:
   \`\`\`
   alembic -c app/alembic.ini upgrade head
   \`\`\`

   For an existing database that was created by the former automatic
   `Base.metadata.create_all` startup path, first verify that it matches the
   baseline tables (`users`, `chat_sessions`, and `chat_messages`), then adopt
   the baseline and apply later migrations:
   \`\`\`
   alembic -c app/alembic.ini stamp 0001_initial_schema
   alembic -c app/alembic.ini upgrade head
   \`\`\`

6. Run the server:
   \`\`\`
   python run_dev.py
   \`\`\`

   API docs available at `http://127.0.0.1:8002/docs`.

   On Windows, run `.\.venv\Scripts\python.exe run_dev.py`. The launcher uses
   `127.0.0.1:8002`, matching the frontend development configuration. The equivalent
   command is `python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8002`.
   Running Uvicorn without `--port` uses 8000, which may already belong to another
   app. An occupied or Windows-reserved port can cause WinError 10013/10048.
   Docker Compose and Railway keep their separately configured ports.

   `database_migration_check_failed` with connection refused/timeout means the API
   could not reach PostgreSQL at startup. Check `docker compose ps postgres`, then
   repeat step 4 before migrations or starting Uvicorn. Verify database availability
   through `http://127.0.0.1:8002/health/ready`; `/health` only checks the API process.
   Database connection attempts (including Alembic) time out after 5 seconds;
   pooled API connections are checked before reuse after a database restart.

## Docker Compose deployment

The Compose stack runs the FastAPI backend, PostgreSQL, and Ollama as separate
services. Ollama is intentionally not bundled into the backend image: its runtime and
multi-gigabyte models would make the application image impractical, and updating a model
would otherwise require rebuilding the application. The official `ollama/ollama` image
stores downloaded models in the named `ollama_models` volume so they survive container
replacement and restarts.

Create the runtime environment file from the existing example and replace every secret or
deployment-specific value before starting the stack:

```powershell
Copy-Item .env.example .env
```

At minimum, set strong values for `DB_PASSWORD` and `JWT_SECRET_KEY`. Configure
`ALLOWED_ORIGINS`, SMTP credentials, and `USDA_API_KEY` for the deployment as needed.
The root `.env` is read by Compose at runtime and is excluded by both `.gitignore` and
`.dockerignore`; it is never copied into an image layer. Do not put credentials in the
Dockerfile or `docker-compose.yml`.

The timeout/residency settings are important on CPU hosts, especially after an Ollama or
full-stack restart, when the first request for each model is guaranteed to load that model
from disk into RAM:

- `OLLAMA_KEEP_ALIVE=1h` keeps a recently used model resident between requests.
- `OLLAMA_TIMEOUT_SECONDS=300` covers cold text-model loads and inference.
- `OLLAMA_CHAT_VISION_TIMEOUT_SECONDS=660` bounds slow image-chat requests at 11 minutes.
- `OLLAMA_VISION_TIMEOUT_SECONDS=660` applies the same bound to meal-photo requests.

Structured vision generation disables model thinking and is capped at 1024 tokens. Qwen3-VL may
return schema-conforming output in Ollama's `thinking` field with an empty `content` field; the
backend validates either field against `VisionResult` before rendering it. Very large images may
still exceed the timeout on CPU-only hosts and return a retryable 503 response.

Start PostgreSQL and Ollama first, pull the configured models once into the persistent
volume, and then start the full stack:

```powershell
docker compose up -d postgres ollama
docker compose exec ollama ollama pull qwen3:8b
docker compose exec ollama ollama pull qwen3-vl:4b
docker compose up -d --build
docker compose ps
curl.exe --fail http://localhost:8000/health
```

The backend connects to `postgres:5432` and `ollama:11434` over the Compose network—not
to `localhost`. Its entrypoint runs `alembic -c app/alembic.ini upgrade head` before
Uvicorn. The shell uses fail-fast mode, so a failed migration exits the container and the
API never starts against a stale schema. Inspect failures with `docker compose logs backend`.

After startup, verify authenticated text chat, text meal logging, and image chat through
the API at `http://localhost:8000/docs`. Meal nutrition requires `USDA_API_KEY`; without
it, the existing graceful unmatched-item behavior remains in effect. The first request for
each Ollama model can take several minutes on a CPU-only 16 GB machine; later requests in
the keep-alive window should be faster.

This Compose configuration uses Ollama on CPU. GPU passthrough is intentionally not enabled
for this laptop. When deploying to GPU-backed infrastructure, add the platform-appropriate
GPU device/runtime configuration to the Ollama service; no GPU runtime belongs in the
FastAPI image.

## Frontend setup

The frontend lives in the separate `food-ai-ui` repository (a sibling checkout
next to this one, e.g. `../food-ai-ui`):

\`\`\`
cd ../food-ai-ui
npm install
npm start
\`\`\`

App available at `http://localhost:4200`, or the URL printed by Angular if that
port is already in use. With `APP_ENVIRONMENT=development`, the API accepts
HTTP(S) loopback origins on any port, including `http://localhost:55035`.
For production or staging, add the exact frontend origin to `ALLOWED_ORIGINS`.

## Chat document import

- Paste text directly into the normal **Ask anything or paste content** composer.
  There is no separate document-creator control in the chat UI.
- Use the paperclip (**Attach image or document**) or drag one file into the same
  composer, add the question/instruction, then press **Send**. Supported documents
  are PDF, DOCX, XLSX, CSV, PPTX, TXT, and Markdown, up to 15 MB. Images remain
  supported up to 8 MB.
  Uploaded files and responses are kept in normal conversation history.
- Document text and structured tables are extracted with format-specific parsers.
  Searchable PDFs use their text layer; scanned PDF pages use the existing OCR fallback.
  Follow-up questions in the same chat receive recent uploaded-document text as
  untrusted evidence through the existing NVIDIA-primary, Ollama-fallback provider chain.
  Upload a PDF with an instruction such as `Extract this PDF table to Excel` to receive
  a validated XLSX attachment. The original PDF remains unchanged.
- XLSX workbooks can also be reformatted through the same Send flow without an AI
  provider. Ask for center/left/right alignment, smart professional formatting,
  header styling, auto-fit column widths, wrapped text, borders, or frozen headers.
  Every worksheet and populated cell is retained, formulas remain formulas, and the
  bot returns an `-updated.xlsx` attachment for download. To target one column, use
  wording such as `Center align column B`.
- To split an item list, make sure the table has a header such as `Category`,
  `Item Category`, or `Category Name`, then ask `Split the item list category-wise
  and create a separate sheet for each category`. The original uploaded attachment
  remains unchanged. The generated workbook contains one formatted worksheet per
  category, including the matching header and item rows.
- PDF, DOCX, XLSX, CSV, PPTX, TXT, and Markdown generation and direct source export
  remain backend API capabilities for
  API clients, but they are no longer exposed as a separate chat-composer workflow.
  Generated output is reopened with its matching parser before it is saved, uses the
  correct extension and MIME type, and is returned through the normal attachment and
  download flow. An optional `filename` is sanitized and its extension is replaced with
  the requested output type.
  A source export uses the **full extracted text**, not the shortened chat preview,
  and does not require AI. The original file's layout/images are not reproduced.
  Newly generated PDFs use the approved A4 navy-and-gold design: a dated cover with
  the document's title, navy section bands, 10-point body text, alternating table rows,
  Active/Inactive badges, repeated table headers, and Page X of Y footers. This applies
  to AI-generated reports/plans, text exports, Markdown tables, and price-wise menus.
  Wide tables use readable portrait panels with the first column repeated. Office-to-PDF
  conversions preserve the source layout instead of applying a new design.
  The embedded PDF font supports Windows-1252 text and the rupee sign. If the text contains unsupported
  characters (for example Tamil, Chinese, or emoji), export as Word (.docx) instead;
  PDF creation reports a clear error rather than silently replacing text with boxes.
- Backend AI document operations have a total 45-second
  deadline (`DOCUMENT_AI_TIMEOUT_SECONDS`), including provider fallback. A timeout
  cancels the upstream connection; an incomplete AI answer is never saved as a finished document. File
  extraction and rendering are separate from this AI deadline. Import-then-create
  can use two AI operations; if import analysis fails, creation stops immediately
  with the imported file saved, rather than starting a second failing AI request.
  AI source text is limited to 30,000 characters, with an explicit truncation notice
  when necessary. Full extracted text remains stored and available for direct export;
  an AI response based on an excerpt must not claim it read the complete document.
- Uploads save the original file, full extracted text, and chat turn **before**
  waiting for AI analysis. If AI fails, the upload still succeeds and shows a
  clearly labelled extracted-text preview, not a fabricated answer. Use the file
  card's **Download** button for the original, or export its saved text. Extraction
  or validation failures keep the selected file and note available to retry.
  Uploaded attachments and generated documents remain saved with the conversation.
- Swagger: `POST /chat/documents` accepts multipart `analyze=false` for import
  without AI. Successful uploads include `analysis_status` (`complete`,
  `unavailable`, or `skipped`). AI failure alone no longer makes an upload return
  503. `POST /chat/documents/generate` accepts
  `{"mode":"export","source_document_id":123,"output_format":"pdf"}`. Supported
  `output_format` values are `pdf`, `docx`, `xlsx`, `csv`, `pptx`, `txt`, and
  `markdown`. This request exports an owned uploaded file's full extracted text;
  `instruction` may be
  omitted. The source must belong to the requested chat when `session_id` is supplied.
- Multi-step requests use `POST /chat/documents/automate` and the same validated pipeline engine
  as `POST /chat/documents/pipeline`. The assistant can resolve the latest file, exact filenames,
  ordinal references such as `the first PDF`, and collective references such as `both PDFs`.
  Ambiguous requests return one clarification question and use persisted chat history to resolve
  the reply. Optional `clarification.options` supplies 2–5 choices; a plain question remains valid.
  Ready requests execute immediately; no review or Build click is required. Clarification choices
  appear in the chat composer only when essential information is missing. The legacy `confirm`
  field remains accepted, but does not gate execution. Requests for both chat text and a file
  return the completed text steps followed by downloadable attachments; a later file failure
  still returns the text already extracted. Explicit exports of the same extracted text to
  PDF/Word preserve that text without a second model rewrite. Every generated
  intermediate is validated and saved, while all useful outputs are
  returned as normal downloadable chat attachments. See
  [Phase 5 document automation](docs/universal-document-phase5.md). Production
  hardening, fidelity rules, security limits, and final acceptance evidence are
  recorded in the [Phase 6 report](docs/universal-document-phase6.md).
- DOCX/PPTX-to-PDF conversion requires headless LibreOffice. The converter detects standard
  Windows installations and `soffice` on `PATH`; set `LIBREOFFICE_BINARY` for a custom location.
- **Extract Excel records by ID:** upload an XLSX file and request, for example,
  `Find the rows with IDs 24138, 24101, 24102 and create a new Excel file`.
  Review and Build copies complete matching rows directly from the workbook, across
  all sheets, including duplicates and blank cells. Column names/order, cell values
  and cell formatting are preserved; the original upload stays unchanged. Missing
  IDs are reported. This operation does not ask an AI to recreate spreadsheet data.
  Specify `column "Dish ID"` or `sheet "Items"` to resolve ambiguous sources.
  Formula cells are exported as their saved values; missing cached results require
  calculating and saving the source in Excel before upload.
  Data-only requests such as `Get data for 24908, 24697, 24702 and 19534` or
  `Give me these 24908, 24697 IDs' row data only` display matching cell values
  immediately and provide a filtered XLSX download. This works during upload and
  as a follow-up, without AI availability or a Build step. New lookups search the
  latest original uploaded workbook; name a generated workbook explicitly to search
  that output instead. Explicit file-creation requests still use Review and Build.
- **Separate Excel rows by price:** upload an XLSX workbook and write
  `Put items with the same price into separate sheets` or
  `same rate items thani thani sheet la list out panni kudu`.
  The app copies actual rows into one tab per price, including a `Blank` tab for missing
  prices, and retains the original worksheets, columns, cell values and formatting.
  It validates the saved workbook before returning the download. This operation does
  not regenerate rows through AI or search the web. Requests to sort within the same
  sheet retain their existing behaviour. Detailed automation prompts support up to
  20,000 characters; ambiguous source worksheets require clarification.

- **Add Excel records:** upload an XLSX workbook and write `Add new rows to sheet "Items"`,
  followed on the next lines by rows copied from Excel, CSV, a Markdown table, or JSON records.
  Include column headers when mapping only selected columns. Review and Build creates an
  `-updated.xlsx` attachment, saves its data/history, and leaves the original upload unchanged.
  Existing cells (including exact decimal values), formulas, styles, and other workbook parts
  are preserved. Formula-only template rows are filled after the last existing record.
  Missing rows or ambiguous sheet/column mappings ask for clarification instead of returning
  an empty-edit 422 error. Appending supplied records does not depend on AI generation.
  Each request supports up to 500 new rows; append operations do not update existing records
  with the same code or invent values for omitted columns.
- **Create files from images:** attach a JPEG, PNG, WebP, or GIF (up to 8 MB) and ask,
  for example, `Read this image and create an Excel sheet with item name and country name`.
  Review the plan, click **Build my document file**, then **View** or **Download** the result.
  If the wizard asks for a source, use its **Upload image or document** button;
  it opens a file picker instead of asking for a typed answer. Attaching an image
  through the composer while a document request is pending also resumes that request,
  including earlier format choices, without needing to repeat the prompt.
  Word and PDF use the same flow. Ordinary image questions still return a chat answer.
  To create a new file from written requirements, no upload is required. A missing image
  mentioned only as a layout example uses a standard layout. If an earlier request is
  waiting for a source, choose **Create without uploading** to generate from written requirements.
  This chooses `source_mode: "description"` on `/chat/documents/automate` and persists
  that choice across reloads; do not combine it with `source_document_id`.
  Extracting actual image data still requires an uploaded image.
  Images already sent in this chat can be used with `Create a Word document from this image`.
  Original image bytes and generated files are saved with the conversation.
  Image extraction is deferred until Build; `analyze=false` does not imply it has been read.
  Local OCR preserves caption positions, with vision-provider fallback for photos or
  insufficient readable text. Review extracted labels and numbers; animated GIFs use the
  first frame. File creation still requires the configured text AI provider.
  On Windows, install Tesseract with
  `winget install --id UB-Mannheim.TesseractOCR --exact --source winget`.
  The image reader discovers standard Windows locations or `PATH`; for a custom location,
  set `TESSERACT_BINARY` in `.env`. Docker already includes Tesseract.
  Requests about objects, people, scenes or charts use visual analysis even when the
  image also contains readable text. A person's gender or other sensitive attributes
  are not inferred from appearance; printed labels can be transcribed. Uncertain cells
  remain empty with a note, while the other readable cells in that row are retained.
  See [image document verification](docs/image-document-creation.md) for execution evidence.
- TXT and CSV files support UTF-8 (with or without a BOM) and BOM-marked UTF-16
  exports. Legacy `.doc` and `.xls`, password-protected files, corrupt files, and
  files without readable text are not supported; export a supported readable copy.
- Scanned PDF pages require the **Tesseract system executable**, not just the
  `pytesseract` Python package. Ensure `tesseract --version` works in the same
  terminal used to start the backend, then restart the backend. The Docker image
  already installs Tesseract. Without it, searchable PDFs still work, while scanned
  and mixed PDFs requiring OCR return a clear 503 instead of a misleading corrupt
  file error. OCR defaults to English; other languages require appropriate OCR
  configuration and language data.
- AI summarization and generation still require the configured LLM provider to be
  available. Supported file formats do not imply that every encrypted or scanned
  document can be read without the required local dependencies.
  Direct text and saved-file text export work without an LLM. A provider failure is not a PDF renderer
  error: provider logs record the HTTP status (when available) and error type without
  source text or credentials. For example, distinguish authentication/rate-limit
  responses from transport timeouts before changing model settings.
  A successful NVIDIA `/v1/models` listing alone does not verify inference access:
  test a small chat completion with the same account/key. A 404 reporting that a
  serving function was not found for the account needs provider/account access
  investigation; increasing the document timeout or token limit will not fix it.

## Pre-commit checks

Install the development dependencies and enable the repository hooks once per clone:

\`\`\`powershell
pip install -r requirements-dev.txt
pre-commit install
\`\`\`

The hooks check staged backend Python with Ruff and type-check it with MyPy. Frontend
linting/formatting hooks live in the separate `food-ai-ui` repository. To check the
entire repository without making a commit, run:

\`\`\`powershell
pre-commit run --all-files
\`\`\`

## CI and testing

Tests use an isolated in-memory SQLite database — no real database connection required.

\`\`\`
python -m pytest -v
\`\`\`

The required CI job runs quality gates in fail-fast order: dependency audit, linting,
type checking, database migrations, and unit tests with coverage. Green required CI
means all of these checks pass:

```powershell
# From the repository root
pip-audit -r requirements.txt
ruff check .
ruff format --check .
mypy app
python -m pytest -v --cov=app --cov-report=term --cov-fail-under=90
```

CI additionally applies every Alembic migration to a PostgreSQL service before running
the test suite. The coverage gate currently requires 90% backend coverage.

The Angular frontend (`food-ai-ui` repository) has its own CI workflow covering
linting, type checking, unit tests, the production build, and a non-blocking
Playwright end-to-end suite — see that repository's README.

## Observability

- `GET /health` and `GET /health/live` are liveness checks. They confirm the API
  process is running and intentionally do not contact external dependencies.
- `GET /health/ready` checks PostgreSQL and Ollama. It returns HTTP 503 with a
  per-dependency status when either service is unavailable.
- `GET /metrics` exposes Prometheus request counts, response-status counts, and
  latency metrics. The metrics endpoint is excluded from its own measurements.

Application logs use Python's standard `logging` API with the JSON formatter configured
in `app/logging_config.py`. New logs should use stable event-style messages and put
searchable context in `extra` fields; credentials, tokens, and uploaded content must not
be logged.

## Authentication flow

1. \`POST /users/\` — register a new user (password is hashed with bcrypt before storage).
2. \`POST /users/login\` — returns a short-lived JWT access token and an opaque refresh token.
3. \`POST /users/refresh\` — exchanges an active refresh token for a new access token.
4. \`POST /users/logout\` — revokes the stored refresh-token hash.
5. \`POST /chat/\` — requires \`Authorization: Bearer <access-token>\`.
6. `POST /meals/` — parses meal text with Ollama, then obtains nutrition values only from USDA FoodData Central.

The Angular frontend attaches access tokens automatically and performs one silent refresh-and-retry when an API request returns 401.

## Security notes

- Passwords are hashed with \`bcrypt\` — never stored in plain text.
- \`.env\` is excluded from version control via \`.gitignore\`.
- If you ever commit a real secret by mistake, rotate it immediately (change the password / regenerate the JWT secret) rather than relying on removing it from git history.

## Vision request hardening and local load check

Chat-image uploads default to a stricter `2/minute` limit. Meal-photo and chat-image
inference also share one in-process queue slot. This deliberately makes later requests
wait under load, but on a CPU-only host it prevents parallel Ollama jobs from competing
for the same cores and making every inference substantially slower. The queue is local
to each API process; a multi-worker deployment needs a distributed queue or semaphore.

Both paths reject files larger than 8 MB and verify the decoded image and its real format
with Pillow before sending bytes to Ollama. Ollama calls have hard request timeouts:
`OLLAMA_VISION_TIMEOUT_SECONDS` for meal photos and
`OLLAMA_CHAT_VISION_TIMEOUT_SECONDS` for general chat images. A timeout returns a clear
503 response instead of leaving the HTTP request hanging indefinitely.

To record a sequential local baseline, start the API, obtain an access token, and run the
following from PowerShell with a genuine test image. Keep the image and prompt unchanged
between runs:

```powershell
$headers = @{ Authorization = "Bearer $env:FOOD_AI_TEST_TOKEN" }
1..5 | ForEach-Object {
    $elapsed = Measure-Command {
        curl.exe -sS -o NUL -H "Authorization: $($headers.Authorization)" `
          -F "image=@C:\path\to\test-image.png;type=image/png" `
          -F "message=Describe this image" http://127.0.0.1:8000/chat/vision
    }
    "run=$($_) elapsed_seconds=$([math]::Round($elapsed.TotalSeconds, 2))"
}
```

Baseline history on 2026-09-01 (Windows, CPU-only, 16 GB RAM): before the larger models
were installed, the chat-vision availability check returned the graceful unavailable error
in 2.099 seconds and the meal-vision check returned it in 2.026 seconds. After installing
`qwen3:8b` and `qwen2.5vl:7b`, a real cold text request exceeded the full 300-second
deadline and returned the typed `ChatModelUnavailableError` instead of crashing the API.
This verifies graceful degradation but also shows that successful cold inference is not
guaranteed within 300 seconds on this machine. Repeat the five-request command above after
the model is warm to record representative successful inference timings.

## Phase 4 documents

| Environment variable | Default |
|---|---|
| `ENABLE_FILE_CLARIFY` | `true` |
| `ENABLE_TABLE_REVIEW` | `true` |
| `ENABLE_SANDBOX_EXECUTION` | `false` |
| `SANDBOX_IMAGE` | `food-ai-sandbox:latest` |
| `SANDBOX_TIMEOUT_SECONDS` | `120` |
| `SANDBOX_MEMORY` | `2g` |
| `SANDBOX_CPUS` | `2` |
| `SANDBOX_SEND_SAMPLES` | `true` |
| `ENABLE_DOCUMENT_CITATIONS` | `false` |
| `DOCUMENT_GROUNDING_MIN_SCORE` | `0.35` |
| `DOCUMENT_ATTRIBUTION_MIN_SUPPORT` | `0.45` |
| `ENABLE_LIBRARY` | `false` |
| `STORAGE_BACKEND` | `database` |
| `STORAGE_LOCAL_DIR` | `data/blobs` |
| `STORAGE_SIGNED_URL_TTL_SECONDS` | `300` |
| `AWS_S3_BUCKET` | `` |
| `AWS_REGION` | `` |
| `ENABLE_BACKGROUND_DOCUMENT_PROCESSING` | `false` |

Clarification and knowledge-only table review extend existing generation and default on. All other capabilities are opt-in. Use `ENABLE_LIBRARY` for the library and `ENABLE_DOCUMENT_CITATIONS` for grounded answers. Citations validate all markers and refuse unsupported answers. Support thresholds control grounding and marker attribution.

Migrate to `0021_documents_phase4` before enabling these features. Local/S3 reads check the storage key and fall back to legacy database bytes. New writes fall back to the database when optional storage is unavailable. Install `requirements-storage.txt` only for S3; AWS credentials use the normal environment/instance credential chain. `python -m scripts.migrate_blobs_to_storage --dry-run` reports a copy count. Omit `--dry-run` to copy idempotently. Keep the original storage available during downgrade, which materializes external bytes before removing storage columns.

Build the sandbox with `docker build -t food-ai-sandbox:latest docker/sandbox`. Only its image needs pandas/openpyxl. Scripts run without network, with a read-only root, a temporary input/output mount, CPU/memory caps, wall timeout and truncated stdout. Existing deterministic operations take priority. Set `RUN_SANDBOX_INTEGRATION=true` to opt into the Docker integration test after building the image.

Background uploads use durable database states and Redis notifications when available. One lifespan-managed worker processes extraction/indexing serially, with a database fallback when Redis is missing. Processing questions receive a waiting message. A restart marks interrupted work failed rather than leaving it stuck. The operations tray follows uploads across routes.


### Workspaces (Phase 5)

Apply `0022_workspaces` before enabling `ENABLE_WORKSPACES=true`. The migration
creates My Workspace for existing users and links their conversations and files.
New users receive a default workspace when the feature is enabled. With the flag
off, `/workspaces` returns 404 and the sidebar switcher is hidden.

Choose a workspace in the sidebar to open `/w/:id`. All/Selected document scope is
read fresh for each question; changing it preserves conversation history. Uploads
reuse the existing type/size validation, storage and optional processing queue.
Checksums reject duplicate files within a workspace while allowing them elsewhere.
Search includes document text, conversations, answers, generated files and optional
semantic chunks. Provider outages preserve cached summaries and rule-based actions.

Conversation exports reuse the Markdown/PDF/Word generators. PDF text is stripped
of Markdown and XML-escaped by the existing plain-text renderer. Save as knowledge
creates a searchable snapshot without changing the source conversation. Removing
workspace items removes membership and bookmarks while preserving stored chat/file
history. Archiving hides the workspace; the default workspace cannot be archived.

### Accounts and platform (Phase 6)

Apply `0023_accounts_platform` before enabling the account/platform flags. All
existing users receive the `member` role; promote administrators through your
controlled database administration process (no public role-changing endpoint).
With `ENABLE_ROLES=true`, `/nvidia-chat` and `/admin/health/detailed` and
`/admin/models` require an administrator. Administrator diagnostics disclose only
safe status/model names, never provider responses, keys or connection strings.

OAuth uses Firebase Google/Microsoft popup sign-in and verifies the resulting ID
token server-side with cached Google signing keys, RS256, project audience,
issuer, expiry, subject, authentication time and provider checks. Unverified email
identities cannot create or link accounts. Configure both providers and authorized
web domains in Firebase, then supply the three `OAUTH_FIREBASE_*` settings. Provider
secrets stay in Firebase. No service-account file or existing API key is changed.
See [ID token verification](https://firebase.google.com/docs/auth/admin/verify-id-tokens)
and [Microsoft sign-in](https://firebase.google.com/docs/auth/web/microsoft-oauth).

`POST /users/keys` shows a generated `fai-` key once. Only its SHA-256 hash,
name, prefix and lifecycle dates are stored. `GET /users/keys` never returns the
raw key or hash. `DELETE /users/keys/{id}` revokes it immediately. Authenticate
with `X-API-Key`; a key cannot mint additional keys. Keys follow their owner's
current database role. The lazy `/settings/api-keys` page appears only when enabled.

The optional gateway holds one NVIDIA AsyncClient on a dedicated event loop so
synchronous chat and async streaming share a connection pool and semaphore.
Retries use jittered exponential backoff for 408/409/425/429, 5xx and transport
errors, never other 4xx. Streams are never retried after emitting content. Existing
Ollama fallback and output-limit continuation remain above that transport seam.

Install `requirements-observability.txt` only for tracing. Missing instrumentation
leaves the application operational and logs a safe warning. Request IDs are
validated (up to 128 safe ASCII characters) or generated, included in errors and
logs, and do not change NDJSON event shapes. Keep collector credentials outside
application responses. Metrics remain available as before.

### Invoice extraction API (Phase 7)

`ENABLE_INVOICE_EXTRACTION=false` hides all three routes with 404. To enable the
API, select an existing vision provider/model and set the flag. No voice setting,
provider key or existing chat route is changed. Revision `0024_invoice_extraction`
is a deployment marker only: this API is stateless and creates no tables or
stored documents. Uploaded images, invoice JSON and failed model output are not
logged or persisted.

All routes accept the existing bearer session or an enabled personal API key:

- `POST /documents/extract-invoice`: multipart `file`, a PDF or PNG/JPEG/WebP/
  single-frame GIF. Extension, MIME, image content, upload size, PDF encryption and
  page count are checked. Every PDF page is rendered in order at a bounded image
  size and sent to the configured VLM; excess pages are rejected, never truncated.
- `GET /documents/vlm/provider`: safe provider/model labels, schema/prompt versions
  and document limits. It never returns credentials or internal endpoint URLs.
- `GET /documents/vlm/health`: checks the configured model's availability without
  paid inference; returns 503 when unavailable. It does not probe other providers.

Ollama inference shares the existing meal/chat vision slot. NVIDIA inference uses
the Phase 6 gateway when enabled. Provider selection is explicit; invoice failures
do not silently switch providers. The model receives a versioned invoice schema,
not the unrelated food-photo schema. Page text and previous output are treated as
untrusted evidence. One repair call is permitted after invalid JSON/schema output,
using the same pages. The total deadline includes that repair.

Success returns `schema_version: "1.0.0"`, `prompt_version`, `request_id`, provider,
model, page/image counts, repair metadata, warnings and typed `data`. Invoice
number/dates, parties, line items, currency, totals and payment fields retain only
extracted values; unknown values remain null. Dates use ISO format and decimal
money serializes as strings to preserve precision. Missing expected fields and
inconsistent printed totals produce warnings; they never cause invented values or
silently rewritten arithmetic. An empty extraction is not a successful invoice.

Failures use `{success:false, schema_version, request_id, error:{code,message,
retryable,violations}}`. Error messages and violations never quote invoice or
provider text. Schema failures after one repair return 422, provider failures 502,
and timeouts 504; unsupported formats return 415, oversize uploads 413, and invalid,
encrypted or over-cap documents 422. These routes do not alter NDJSON chat events.
Provider/network tests use fakes; enable a configured VLM to exercise live inference.


### Code Assistant (approved optional extension)

Apply `0025_code_assistant` and set `ENABLE_CODE_ASSISTANT=true` to show the
Code Assistant sidebar entry and `/code` page. All `/code/repositories` APIs
require authentication and return 404 while disabled. Normal chat is unchanged.

An operator provisions a trusted local repository under
`CODE_REPOSITORY_ROOT/<numeric-user-id>/<relative-path>`; the user connects only
the relative path. No remote clone, arbitrary absolute server paths, symlink or
junction repositories are supported. Provision reviewed source code, excluding
credentials. Hidden files, credential-named files, binary/non-UTF-8 files, large
files, dependencies and build folders are excluded. Source files are capped at
200 KB each, 500 files and 10 MB total; scan traversal is capped at 10,000 entries.
Indexing and agent requests are limited to five per minute per client.
The index is a database JSON snapshot, replaced atomically on reindex. Search
is deterministic lexical ranking with file and line references; no Chroma,
Redis, embedding model or additional infrastructure is required. Disconnect
deletes the snapshot registration and preserves source files and Git history.

Routes: GET/POST `/code/repositories`; DELETE `/{id}`; POST `/{id}/index`;
GET `/{id}/files`, `/{id}/file?path=...`, `/{id}/search?q=...`, and
`/{id}/git/{status|diff|log|branches}`; POST `/{id}/agent` with
`{"question":"Explain the authentication flow"}`. Suffixes are relative to
`/code/repositories`. Every lookup is owner-scoped, including API-key auth.
Git runs argument lists without a shell, disables external diff/textconv and
filesystem monitoring, limits runtime to 10 seconds and displayed output to
60 KB. Diff is limited to indexed source paths and compares HEAD to the working
tree (staged and unstaged). Repositories without a commit can still be indexed;
HEAD-based views return a safe 409 until a commit exists.

The agent reuses existing model/provider selection, credentials and gateway.
Its strict tool allowlist is `list_files`, `search_code`, `read_file`,
`git_status`, `git_diff` and `finish`. File tools read the indexed snapshot;
Git tools read the current working tree. Responses include answer, status and
a tool trace. Invalid provider output returns safe 502; the total deadline
returns 504; reaching the step limit returns `status=step_limit`. The agent can
explain code and propose patches as text. Applying patches, command execution,
Git mutations and automatic deployments are outside this read-only version.
No credentials or voice settings are changed. UI output is escaped plain text.
