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
docker compose exec ollama ollama pull llava
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
| `DB_HOST` | Required | PostgreSQL hostname; use `localhost` for local Uvicorn and `postgres` inside Compose. |
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
| `OLLAMA_KEEP_ALIVE` | Optional | How long Ollama keeps a loaded model resident; default `30m`. |
| `OLLAMA_CHAT_THINK` | Optional | Enables Qwen reasoning mode; default `false` for faster chat. |
| `OLLAMA_CHAT_MAX_TOKENS` | Optional | Maximum generated text-chat tokens; default `768`. |
| `OLLAMA_VISION_MODEL` | Optional | Meal-image vision model; default `llava:latest`. |
| `OLLAMA_VISION_TIMEOUT_SECONDS` | Optional | Meal-image request timeout; default `180`. |
| `OLLAMA_CHAT_VISION_MODEL` | Optional | General image-chat model; default `llava:latest`. |
| `OLLAMA_CHAT_VISION_TIMEOUT_SECONDS` | Optional | General image-chat timeout; default `180`. |
| `USDA_API_KEY` | Optional | USDA FoodData Central key; nutrition matches are unavailable when empty. |
| `USDA_API_URL` | Optional | USDA API base URL; default `https://api.nal.usda.gov/fdc/v1`. |
| `USDA_TIMEOUT_SECONDS` | Optional | USDA request timeout; default `15`. |
| `ALLOWED_ORIGINS` | Optional | Comma-separated browser origins allowed by CORS; default `http://localhost:4200`. |
| `LOGIN_RATE_LIMIT` | Optional | Login limit in SlowAPI format; default `5/minute`. |
| `REGISTER_RATE_LIMIT` | Optional | Registration limit; default `5/minute`. |
| `MEAL_CREATE_RATE_LIMIT` | Optional | Meal-creation limit; default `10/minute`. |
| `CHAT_VISION_RATE_LIMIT` | Optional | Image-chat limit; default `2/minute`. |
| `MIGRATION_CHECK_ENABLED` | Optional | Enables the startup warning for pending Alembic migrations; default `true`. |

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
├── food-ai-ui/               # Angular frontend
│   └── src/app/
│       ├── components/         # sidebar, chat-window, chat-input
│       ├── pages/               # home, login, chat
│       └── services/             # auth, chat, interceptor, guard
├── tests/                    # pytest test suite
└── requirements.txt
\`\`\`

## Prerequisites

- Python 3.13
- Docker Engine 24+ with Docker Compose v2.20+ (for the containerized setup)
- Node.js 22.12+ and Angular CLI 22 (for frontend development)
- PostgreSQL running locally
- [Ollama](https://ollama.com) running locally with `qwen3:8b` and `llava:latest` pulled
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
   ollama pull llava
   \`\`\`
   If it is unavailable, text meal logging and the rest of the application continue
   working; image uploads return a clear service-unavailable response.

   General chat image OCR uses the `pytesseract` Python package plus the separate
   Tesseract system executable. Install Tesseract and ensure its executable is on
   `PATH` (for example, install Tesseract OCR on Windows or `tesseract-ocr` through
   your Linux package manager). If the executable is missing, image chat continues
   with vision-only analysis and logs a warning; the application does not fail.

4. Create the \`FoodAI_DB\` database in PostgreSQL (via pgAdmin or \`psql\`).

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

- `OLLAMA_KEEP_ALIVE=30m` keeps a recently used model resident between requests.
- `OLLAMA_TIMEOUT_SECONDS=300` covers cold text-model loads and inference.
- `OLLAMA_CHAT_VISION_TIMEOUT_SECONDS=180` covers general image-chat cold starts.
- `OLLAMA_VISION_TIMEOUT_SECONDS=180` covers meal-photo cold starts.

Start PostgreSQL and Ollama first, pull the configured models once into the persistent
volume, and then start the full stack:

```powershell
docker compose up -d postgres ollama
docker compose exec ollama ollama pull qwen3:8b
docker compose exec ollama ollama pull llava
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

\`\`\`
cd food-ai-ui
npm install
npm start
\`\`\`

App available at \`http://localhost:4200\`.

## Running tests

Tests use an isolated in-memory SQLite database — no real database connection required.

\`\`\`
python -m pytest -v
\`\`\`

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
