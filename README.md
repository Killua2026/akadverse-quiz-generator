# AkadVerse: Quiz Generator

**Tier 5 LLM | Microservice Port: `8016`**

An intelligent AI agent that generates highly creative, university-standard quizzes featuring real-world analogies and plausible distractors to enhance student active learning.

## Table of Contents
- [What This Microservice Does](#what-this-microservice-does)
- [Architecture Overview](#architecture-overview)
- [Prerequisites](#prerequisites)
- [Environment Configuration](#environment-configuration)
- [Installation](#installation)
- [Running the Server](#running-the-server)
- [API Endpoints](#api-endpoints)
- [Testing with Swagger UI](#testing-with-swagger-ui)
- [Example Test Inputs](#example-test-inputs)
- [Understanding the Responses](#understanding-the-responses)
- [Generated Files](#generated-files)
- [Common Errors and Fixes](#common-errors-and-fixes)
- [Project Structure](#project-structure)

## What This Microservice Does

This service is a Tier 5 component of the AkadVerse AI-first e-learning platform, operating within the My Learning module.

Designed for both faculty and students, it acts as an expert university professor to generate rigorous academic quizzes.

**Core Workflow:**

1.  Accepts a topic, difficulty level, and desired question count.
2. Dynamically discovers and caches an ordered list of available Gemini text-generation models during service startup.
3.  Uses a highly creative prompt template to generate questions featuring real-world analogies (e.g., Lagos traffic for algorithm routing).
4. Retries transient provider failures and falls back to the next discovered model.
5. Enforces strict JSON output for frontend safety.
6. Saves the generated quiz to a local SQLite database and returns it to the user.

**Key Design Decisions:**

*   **Dynamic Model Discovery:** Uses the unified `google-genai` SDK to discover and prioritize available text-generation models, preventing 404 errors during Google deprecation cycles.
*   **Startup Readiness:** Model discovery runs during FastAPI startup, so `/health` reports the actual cached candidates before the first `/generate-quiz` request.
*   **Runtime Model Fallback:** Retries temporary `503`, `429`, quota, timeout, and connection failures before trying the next discovered model.
*   **Server-Side Credentials:** Loads `GOOGLE_API_KEY` from `.env`; clients never send Gemini credentials in the request body.
*   **Async Generation:** Uses LangChain `ainvoke()` with a bounded timeout so Gemini calls do not block the FastAPI event loop.
*   **Gemini Structured Output Compatibility:** Uses LangChain's `json_schema` structured-output method and explicitly sets `thinking_budget=0` for quiz generation. This avoids the known structured-output and thinking interaction that can cause requests to hang without returning data.
*   **SDK Compatibility:** Constrains `google-genai` to the current major-version range (`>=1.67.0,<3.0.0`) while the SDK's AFC behavior remains subject to breaking changes in a future 3.x release.
*   **Bounded Input:** Validates topic, difficulty, and question count to control request size and generation cost.
*   **FastAPI Lifespan Manager:** The database is initialized safely during the server startup phase.
*   **SQLite Context Manager:** All database writes are wrapped in a context manager to prevent memory leaks if an LLM error occurs mid-generation.
*   **The Distractor Rule:** The prompt forces the LLM to generate exactly 4 MCQ misconceptions, paired with rich explanations.

## Architecture Overview

| Component      | Technology             | Purpose                      |
| :------------- | :--------------------- | :--------------------------- |
| API Layer      | FastAPI (Python 3.10+) | Async REST endpoints         |
| Core AI        | Gemini (via google-genai & LangChain) | Reasoning and structured generation |
| Storage        | SQLite (akadverse_question_bank.db) | Question bank persistence |
| Data Validation| Pydantic             | Strict input/output schema enforcement |

*Note:* This service publishes a mock `assessment.generated` Kafka event. In production, this event is consumed by the Insight Engine to track curriculum coverage.

## Prerequisites

*   Python 3.10 or higher
*   `pip` (Python package manager)
*   Google Gemini API key (free tier is sufficient)
*   Windows users: Ensure you have activated your virtual environment using `venv\Scripts\activate` before installing dependencies.

## Environment Configuration

1.  Go to [https://aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2.  Sign in with a Google account.
3.  Click Create API Key.
4.  Create a `.env` file inside `akadverse-quiz-generator`:

```env
GOOGLE_API_KEY=your_gemini_api_key_here
MODEL_DISCOVERY_TTL_SECONDS=300
MODEL_REQUEST_TIMEOUT_SECONDS=45
MODEL_RETRY_ATTEMPTS=2
MODEL_RETRY_BACKOFF_SECONDS=1
```

Keep `.env` out of version control. The API loads the key when the service starts, so it is no longer accepted in the `/generate-quiz` request body.

## Installation

**Step 1 -- Set up your project folder**

Create a dedicated folder for this microservice:

```bash
mkdir akadverse-quiz-generator
cd akadverse-quiz-generator
```

**Step 2 -- Create and activate a virtual environment**

*   **Windows:**
    ```bash
    python -m venv venv
    venv\Scripts\activate
    ```
*   **macOS/Linux:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

**Step 3 -- Install dependencies**

```bash
pip install -r requirements.txt
```

**Dependency Map:**

| Package | Purpose |
|---|---|
| fastapi | Web framework |
| uvicorn | ASGI server |
| pydantic | Data validation |
| google-genai>=1.67.0,<3.0.0 | Official Gemini SDK; capped before the planned 3.x breaking changes |
| langchain-google-genai | LangChain wrapper for structured outputs |
| langchain-core | Prompt templates |
| python-dotenv | Loads `.env` configuration |

## Running the Server

With your virtual environment activated, run the following command:

```bash
uvicorn quiz_generator:app --host 127.0.0.1 --port 8016 --reload
```

Expected terminal output:

```
[Startup] AkadVerse Quiz Generator initializing...
[DB] Question bank database initialized successfully.
[Startup] Discovered 2 Gemini text-generation model(s).
[Startup] Ready. Run with: uvicorn quiz_generator:app --host 127.0.0.1 --port 8016 --reload
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8016 (Press CTRL+C to quit)
```

## API Endpoints

### 1. `GET /health`

**What it does:** Confirms that the quiz generator service is running, that startup model discovery completed, and reports its core integrations.

**Success response (200 OK):**

```json
{
  "status": "ready",
  "service": "AkadVerse Quiz Generator",
  "llm": "google-gemini",
  "gemini_configured": true,
  "model_discovery": true,
  "candidate_models": ["gemini-3.8-flash", "gemini-2.5-flash"],
  "selected_model": "gemini-3.8-flash",
  "last_attempted_model": "gemini-3.8-flash",
  "last_successful_model": "gemini-3.8-flash",
  "fallback_count": 0,
  "last_model_error": null,
  "database_status": "ok",
  "database": "sqlite",
  "database_path": "akadverse_question_bank.db"
}
```

Use [http://127.0.0.1:8016/health](http://127.0.0.1:8016/health) for local health checks. `model_discovery` and `candidate_models` are populated during service startup, before any quiz request. `status` is `ready` when the API has a server-side Gemini key, at least one discovered candidate model, and a working database connection; otherwise it is `not_ready`. `selected_model` reports the latest model attempted during `/generate-quiz`, while `last_successful_model` reports the latest model that completed generation.

### 2. `POST /generate-quiz`

**What it does:** Generates a new creative quiz, stores it in the local SQLite database, and returns the structured JSON.

**Request Body (JSON):**

| Field | Required | Default | Description |
|---|---|---|---|
| `topic` | Yes | -- | The subject of the quiz (e.g., 'Deadlocks and Concurrency'). |
| `difficulty` | Yes | -- | The academic level (e.g., '300-level University Standard'). |
| `question_count` | No | 3 | The number. |

**Success response (200 OK):**

```json
{
  "topic": "Operating Systems: Deadlocks",
  "difficulty": "300-level University Standard",
  "questions": [
    {
      "question_text": "Which of the following scenarios best illustrates the 'circular wait' condition of a deadlock?",
      "question_type": "MCQ",
      "options": [
        "Option A",
        "Option B",
        "Option C",
        "Option D"
      ],
      "correct_answer": "Option B",
      "explanation": "Detailed pedagogical explanation of why B is correct and the others are common misconceptions."
    }
  ]
}
```

Expected terminal output:

```
[Model] Selected AI Model dynamically: gemini-2.5-flash
[Generate] Calling Gemini API using gemini-2.5-flash for topic: Operating Systems: Deadlocks...
[KAFKA MOCK] Published event 'assessment.generated' for topic: Operating Systems: Deadlocks
```

## Testing with Swagger UI

With the server running, open your browser to:
[http://127.0.0.1:8016/docs](http://127.0.0.1:8016/docs)

## Example Test Inputs

**Test 1 -- Generate a Creative Quiz**

Expand `POST /generate-quiz` and click Try it out. Paste the following JSON:

```json
{
  "topic": "Operating Systems: Deadlocks and Concurrency",
  "difficulty": "300-level University Standard",
  "question_count": 3
}
```

Expected: A 200 OK response containing exactly 3 questions. Verify that the explanations explicitly break down the distractors and use a real-world analogy. Temporary Gemini capacity failures are retried and can fall back to another discovered model.

## Understanding the Responses

*   **Why are the distractors so specific?**
    The `prompt_template` enforces "The Distractor Rule". Instead of hallucinating random incorrect answers, the LLM is instructed to design options based on common student misconceptions. This transforms the quiz from a simple assessment tool into an active learning engine.

*   **The `[KAFKA MOCK]` line**
    This log simulates Apache Kafka event publishing. In a production AkadVerse notifies downstream consumers (like the Insight Engine) that a new assessment module has been generated and is ready for student consumption.

  *   **The `503` response from Gemini**
    The service retries transient provider failures and tries the next discovered model. If every candidate is temporarily unavailable, the API returns `503` with a retry-later message instead of exposing provider details.

  *   **The health response**
    `selected_model` is the latest model attempted, while `last_successful_model` is the latest model that successfully generated a quiz. `candidate_models` shows the current cached fallback order.

## Generated Files

| File / Folder | What it is | Gitignore? |
| :------------ | :--------- | :--------- |
| `akadverse_question_bank.db` | SQLite database | Yes -- never commit |
| `__pycache__/` | Python bytecode cache | Yes |

**CRITICAL:** The `akadverse_question_bank.db` file will be created automatically on your first successful run. Do not commit this database to version control, as it will contain local state.

## Common Errors and Fixes

*   **Error: `ModuleNotFoundError: No module named 'google.genai'`**
    You are missing the unified Google GenAI SDK. Install it:
    ```bash
  pip install "google-genai>=1.67.0,<3.0.0"
    ```

*   **Generation hangs or ends with a timeout**
  This service uses `method="json_schema"` instead of the deprecated `json_mode` method and passes `thinking_budget=0` to the LangChain Gemini client. These settings avoid the known structured-output and thinking interaction that can produce a request with no returned bytes. The `google-genai` dependency is also capped below version 3 to avoid unreviewed AFC breaking changes.

*   **Error: `SQLite Database is Locked`**
    Another process is holding the database connection open. The new `get_db()` context manager prevents this internally, but if you have an external SQLite viewer open, close it and try again.

*   **Address already in use on startup**
    Port 8016 is occupied by another service. Either stop the conflicting service or run the application on a different port:
    ```bash
    uvicorn quiz_generator:app --host 127.0.0.1 --port 8015 --reload
    ```

## Project Structure

```
akadverse-quiz-generator/
|
|-- quiz_generator.py          # Main microservice - all logic here
|-- requirements.txt           # Python dependencies
|-- README.md                  # This file
|-- .gitignore                 # Excludes DB and generated files
|
|-- akadverse_question_bank.db # SQLite database - DO NOT COMMIT
```

## Part of the AkadVerse Platform

This microservice is Tier 5 in the AkadVerse AI architecture, operating within the My Learning module alongside:

*   Concept Explainer
*   External Resources Puller
*   Note-to-Audio Converter

The `assessment.generated` Kafka event published by this service is consumed by the Insight Engine in production. During local development, it is simulated as a terminal log line.

---

AkadVerse AI Architecture -- v1.0