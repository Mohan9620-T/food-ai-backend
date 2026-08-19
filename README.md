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
   JWT_SECRET_KEY=a_long_random_secret_string
   SMTP_HOST=smtp.example.com
   SMTP_PORT=587
   SMTP_USERNAME=your_smtp_username
   SMTP_PASSWORD=your_smtp_password
   SMTP_FROM_EMAIL=no-reply@example.com
   SMTP_USE_TLS=true
   \`\`\`

   SMTP settings are optional for local development. When configured, a newly
   registered user receives an email containing the submitted login credentials.

4. Create the \`FoodAI_DB\` database in PostgreSQL (via pgAdmin or \`psql\`).

5. Run the server:
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
2. \`POST /users/login\` — returns a JWT access token (\`Bearer\` type, 60-minute expiry).
3. \`POST /chat/\` — requires \`Authorization: Bearer <token>\` header.

The Angular frontend handles this automatically via an HTTP interceptor and route guard — login once, and the token is attached to every request until it expires or you log out.

## Security notes

- Passwords are hashed with \`bcrypt\` — never stored in plain text.
- \`.env\` is excluded from version control via \`.gitignore\`.
- If you ever commit a real secret by mistake, rotate it immediately (change the password / regenerate the JWT secret) rather than relying on removing it from git history.
