# Food AI Backend

A full-stack food & nutrition assistant with a FastAPI backend, Angular frontend, JWT authentication, and a local Ollama-powered AI chat.

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

- Python 3.11+
- Node.js 18+ and Angular CLI
- PostgreSQL running locally
- [Ollama](https://ollama.com) running locally with a chat model pulled (e.g. \`llama3.2\`)

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

3. Create a \`.env\` file in the project root (never commit this file):
   \`\`\`
   DB_HOST=localhost
   DB_PORT=5432
   DB_NAME=FoodAI_DB
   DB_USER=postgres
   DB_PASSWORD=your_postgres_password
   DATABASE_URL=postgresql://postgres:your_postgres_password@localhost:5432/FoodAI_DB
   JWT_SECRET_KEY=a_long_random_secret_string
   ACCESS_TOKEN_EXPIRE_MINUTES=30
   REFRESH_TOKEN_EXPIRE_DAYS=7
   REMEMBERED_ACCESS_TOKEN_EXPIRE_DAYS=30
   SMTP_HOST=smtp.example.com
   SMTP_PORT=587
   SMTP_USERNAME=your_smtp_username
   SMTP_PASSWORD=your_smtp_password
   SMTP_FROM_EMAIL=no-reply@example.com
   SMTP_USE_TLS=true
   ALLOWED_ORIGINS=http://localhost:4200
   LOGIN_RATE_LIMIT=5/minute
   REGISTER_RATE_LIMIT=5/minute
   MEAL_CREATE_RATE_LIMIT=10/minute
   USDA_API_KEY=your_fooddata_central_api_key
   OLLAMA_VISION_MODEL=llava:latest
   OLLAMA_VISION_TIMEOUT_SECONDS=180
   \`\`\`

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

## Frontend setup

\`\`\`
cd food-ai-ui
npm install
ng serve
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
