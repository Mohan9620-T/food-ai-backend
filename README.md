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
| `APP_ENVIRONMENT` | Optional | Set to `production` to enforce NVIDIA-first, Ollama-fallback routing regardless of `LLM_PROVIDER`; default `development`. |
| `LLM_PROVIDER` | Optional | Development override: `ollama` is local-only; `nvidia` uses NVIDIA first with one Ollama fallback. |
| `NVIDIA_API_KEY` | Required for NVIDIA | NVIDIA API credential. Never commit a real value. |
| `NVIDIA_API_BASE_URL` | Optional | NVIDIA OpenAI-compatible API base URL. |
| `NVIDIA_CHAT_MODEL` | Optional | NVIDIA text model; defaults to `nvidia/nemotron-3-super-120b-a12b`. |
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
| `NVIDIA_CHAT_REASONING_BUDGET` | Optional | Brief reasoning for `nvidia/nemotron-3-super-120b-a12b` text chat; default `1024`, range `0`-`8192`. Uses low-effort reasoning; `0` disables it. Other models and atomic document requests retain their existing settings. Reasoning is not displayed or saved in chat history. |
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
   python -m uvicorn app.main:app --reload
   \`\`\`

   API docs available at \`http://127.0.0.1:8000/docs\`.

   `database_migration_check_failed` with connection refused/timeout means the API
   could not reach PostgreSQL at startup. Check `docker compose ps postgres`, then
   repeat step 4 before migrations or starting Uvicorn. Verify database availability
   through `http://127.0.0.1:8000/health/ready`; `/health` only checks the API process.
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
  The current PDF font supports Windows-1252 text. If the text contains unsupported
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
