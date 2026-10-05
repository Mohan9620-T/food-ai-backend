# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `docs/architecture.md`: a verified overview (with Mermaid diagrams) of the chat
  memory system, NVIDIA/Ollama provider routing, and the document upload/retrieval
  pipeline, linked from the README.
- Documented `CONTEXT_TOKEN_BUDGET`, `CONTEXT_TOKEN_CHAR_DIVISOR`,
  `ENABLE_REDIS_CACHE`, and `ENABLE_SEMANTIC_RAG` in the README environment-variable
  reference; they were implemented but undocumented.
- Explicit per-section token-budget shares for chat context assembly: document
  and saved-chat references are capped to their own share of the context budget
  so a large semantic-RAG retrieval cannot crowd out persistent user memory, the
  rolling summary, or recent conversation history.
- Food/diet-specific emotional guidance: the assistant no longer answers guilt or
  body-image distress with a calorie number, restriction target, or diet correction
  offered as comfort, while still answering calm nutrition questions normally, and
  routes patterns beyond ordinary diet frustration to the existing safety guidance.
- Persistent per-user chat memory (`user_memory_facts`): allergies, dietary
  restrictions, dislikes, goals, and cooking constraints stated once in chat are
  extracted deterministically (no extra LLM call) and recalled in later sessions
  instead of aging out of the rolling conversation summary.
- Configurable `LLM_PROVIDER` routing for general image chat, with local Ollama and
  hosted NVIDIA vision provider implementations and provider-specific tests.
- A production Docker Compose configuration using file-based secrets, with deployment
  documentation and settings coverage.
- A non-blocking Playwright user-flow suite and CI browser-test integration.

### Changed

- Documented the production quality, security, test, and deployment workflows in the
  project onboarding guide.
- Made the NVIDIA API base URL configurable and updated its default vision model and
  request timeout settings.

## [0.1.0] - 2026-09-04

### Added

- FastAPI and Angular application with JWT authentication, refresh tokens, profiles,
  and account email support.
- Ollama-powered conversational chat with persisted sessions, streaming responses,
  image uploads, restored image history, drag-and-drop, retry support, and enlarged
  image previews.
- Meal logging, USDA-backed nutrition lookup, personalized diet plans, structured
  vision results, and normalized food names.
- Browser speech recognition with progressive dictation and explicit language switching.
- Docker Compose development stack for the backend, PostgreSQL, and Ollama, including
  Alembic-managed database migrations and documented Windows setup.
- Backend and frontend unit tests, linting, type checking, pre-commit hooks, dependency
  audits, secret scanning, Dependabot updates, and enforced coverage thresholds.
- Prometheus request metrics plus separate liveness and dependency-readiness endpoints.
- Vision evaluation datasets, regression results, model comparisons, production tuning,
  and fine-tuning assessment documentation.

### Changed

- Selected Qwen3-VL 4B as the configured vision model and reduced large-image inference
  latency through image preprocessing and runtime tuning.
- Expanded FastAPI route documentation and configuration/onboarding references.
- Made English the default response language and require an explicit user request before
  changing conversation language.

### Fixed

- Preserved in-progress and completed chat history across refreshes without duplicating
  messages or switching to a different chat session.
- Persisted uploaded chat images so they remain available when history is reopened.
- Corrected vision-model error handling, structured image responses, and image-question
  intent handling.
- Made dictated speech render progressively, prevented duplicated fragments, and allowed
  microphone input to recover after recognition errors.
- Removed plaintext passwords from account email content.

### Security

- Added password hashing, authenticated API dependencies, sanitized environment templates,
  automated dependency audits, continuous Gitleaks scanning, and weekly dependency updates.

[Unreleased]: https://github.com/Mohan9620-T/food-ai-backend/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Mohan9620-T/food-ai-backend/releases/tag/v0.1.0
