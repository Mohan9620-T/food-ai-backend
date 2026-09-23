# Railway deployment

Deploy three services in the same Railway project: **Postgres**, **food-ai-backend**,
and **food-ai-ui**. The backend GitHub source must use the branch containing these
fixes (`feat/universal-document-phase6`), not the older `main` deployment.

## Backend

Use the repository Dockerfile and set these Railway service variables:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
APP_ENVIRONMENT=production
LLM_PROVIDER=nvidia
OLLAMA_REQUIRED_FOR_READINESS=false
PORT=8080
MIGRATION_CHECK_ENABLED=true
ALLOWED_ORIGINS=https://food-ai-ui-production.up.railway.app
```

Set `JWT_SECRET_KEY` to a long random secret and `NVIDIA_API_KEY` to your provider
key using Railway Variables, never in Git. `USDA_API_KEY` is optional for nutrition
lookup. For account emails, configure an HTTPS provider as described in
[email delivery](email-delivery.md); Trial/Hobby do not permit outbound SMTP.
Gmail API, Microsoft Graph, Resend and SMTP are supported. Existing local database data is
not copied automatically; Railway uses its own persistent Postgres database.

Do not paste `None` as a database host/port or use a laptop's `localhost` database.
The complete database URL is preferred; `DB_*` or `PG*` fields are also supported.
The container validates configuration, applies Alembic migrations with bounded
retries, and binds to Railway's `PORT`. `/health/ready` requires PostgreSQL; Ollama
is optional in this hosted NVIDIA configuration. Redis and semantic RAG stay off
unless their services are explicitly configured.

The container includes headless LibreOffice for Word, spreadsheet and presentation
conversion to PDF, along with OCR and DejaVu fonts. CI installs the same conversion
runtime so Linux tests exercise real office-to-PDF conversion.

Uploads, generated files and conversation history are stored in PostgreSQL. Keep
the Postgres volume and enable backups before relying on this deployment for
important data.

## Frontend

Deploy the separate `food-ai-ui` directory with its Dockerfile. Set:

```text
BACKEND_URL=http://${{food-ai-backend.RAILWAY_PRIVATE_DOMAIN}}:8080
PORT=8080
```

Generate a public domain targeting port 8080. The production Angular build uses
`/api`, and its Node server streams requests to `BACKEND_URL` over Railway's private
network. No backend secret or localhost URL is compiled into the browser bundle.
`RAILWAY_PUBLIC_DOMAIN` is automatically permitted by Angular's host validation;
for a custom domain, also set `NG_ALLOWED_HOSTS` to its hostname.

## Verify

Open the UI's `/login` route and check `/health` plus `/api/health/ready`. Register
a test account, log in, send a chat request, upload a document, then download the
generated file. Refresh the page and verify the same account's history.

The backend dependency audit runs before CI tests. The deployed app uses the HTTP
Chroma client; CI provides a separate Chroma service for retrieval tests. To run
these tests locally, start Chroma and set `TEST_CHROMA_PORT` if it differs from
8001. Test collections use unique names and are removed after each test.

Deployment configuration follows [Railway variables](https://docs.railway.com/variables/reference)
and [health checks](https://docs.railway.com/deployments/healthchecks).
