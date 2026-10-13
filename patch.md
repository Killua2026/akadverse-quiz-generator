# Gemini API Resilience Pattern for AkadVerse

This is the reusable implementation note for the Gemini integration we built in this API. It captures the exact pattern we used so Copilot can apply the same approach to other Gemini-backed FastAPI services.

## What we implemented

We upgraded the API from a single hardcoded Gemini model with a single request attempt into a resilient, server-side, observable integration.

### 1. Gemini keys are now server-side only

- We moved the API key to a `.env` file and load it with `python-dotenv`.
- The service reads `GOOGLE_API_KEY` from the environment instead of accepting it in request bodies.
- The request model and API docs no longer expose a `google_api_key` field.
- If the key is missing, the service returns a clear `503 Service Unavailable` instead of failing silently or exposing config details.

This protects credentials and keeps the client contract clean.

### 2. We discover candidate Gemini models instead of assuming one model works

- We list available Gemini models through the unified Google GenAI SDK.
- We strip the `models/` prefix and normalize the names.
- We filter out non-text models such as embeddings, image, vision, speech, TTS, and audio models.
- We keep a preferred ordering so the service tries the best models first.
- We cache the discovered models with a TTL so startup and health checks are fast and do not requery the provider every request.
- We run model discovery during FastAPI startup so `/health` can reflect real readiness before the first business request.

This makes the service resilient when one model is blocked, unavailable, or unsuitable.

### 3. We added retry and fallback across models

- We treat provider issues like `503`, `429`, quota limits, timeouts, and temporary connection failures as transient.
- We retry each candidate a bounded number of times with short exponential backoff.
- If one model fails repeatedly, the service moves to the next candidate in the list.
- We keep a timeout around the async generation call so a hung request cannot block the worker forever.
- If all candidates fail, the API returns a safe `503` retry-later response and logs the full server-side failure without leaking secrets.

This is the key resilience improvement that prevents one provider outage from taking the whole endpoint down.

### 4. We use structured outputs correctly

- We replaced deprecated `json_mode` behavior with `method="json_schema"` for structured Gemini responses.
- We pass the service's Pydantic model into `with_structured_output`.
- We explicitly set `thinking_budget=0` to avoid the known Gemini structured-output hang and zero-byte output problem.
- We validate/normalize the result back into the expected Pydantic model before returning it.

This keeps the API compatible with Gemini structured generation while avoiding the failure mode we encountered.

### 5. We pin the Google SDK version range

- We keep the Google GenAI dependency pinned to a compatible major-version range so future automatic function calling changes do not break the service unexpectedly.
- This avoids unreviewed SDK upgrades causing production regressions.

### 6. Health checks now reflect real runtime readiness

The `/health` endpoint reports operational state, not just a basic process liveness check. It includes values such as:

- `status`
- `gemini_configured`
- `model_discovery`
- `candidate_models`
- `selected_model`
- `last_attempted_model`
- `last_successful_model`
- `fallback_count`
- `last_model_error`
- `database_status`

The service is considered ready only when:

- the Gemini key is configured,
- at least one usable candidate model is discovered,
- local dependencies like the database are healthy.

This gives us a meaningful distinction between a process being alive and the service actually being ready to serve requests.

### 7. Input validation and safety were tightened

- We added bounded Pydantic validation for fields such as topic, prompt, and requested count.
- We preserved the existing business behavior and database writes.
- We keep DB work inside a clean context/transaction pattern.
- We convert provider failures into HTTP statuses like `503` and `429` rather than sending raw provider errors to the client.

This keeps the API stable and avoids sending unsafe or confusing error details to users.

### 8. Startup behavior is clean and operational

- We do startup initialization and model discovery in the FastAPI lifespan hook.
- We do not make a live generation request during startup.
- We only discover and cache valid candidate models so the service is ready to serve real requests later.

This keeps startup lightweight while ensuring `/health` shows useful information immediately.

### 9. Documentation was updated to match the implementation

The README now includes:

- `.env` setup and the server-side `GOOGLE_API_KEY` requirement,
- request examples without an API key,
- model discovery priority and TTL rules,
- retry counts, timeouts, and backoff behavior,
- the structured-output pattern using `json_schema` and `thinking_budget=0`,
- the SDK compatibility range,
- the meaning of model health fields.

---

## Copilot handoff prompt

Use this instruction when applying the same pattern to any other Gemini-based FastAPI service:

- Move the Gemini API key to environment configuration and never accept it in the request body.
- Load the key from `.env` using `python-dotenv` and require it for Gemini-dependent endpoints.
- Discover a list of valid Gemini text-generation models at startup and cache them with a TTL.
- Filter out non-text models and prefer the best available candidates first.
- Add async generation with bounded timeout, retry, and fallback across candidate models.
- Treat `503`, `429`, quota, timeout, and temporary connection errors as transient.
- Use `with_structured_output(..., method="json_schema")` and set `thinking_budget=0` for structured-output requests.
- Keep `/health` informative and ready-based, including candidate model information and fallback state.
- Return safe HTTP failures to clients without exposing provider secrets or raw response details.
- Pin the Google GenAI SDK to a compatible version range.
- Keep the API contract and business logic intact while making the Gemini layer resilient and operationally observable.

## Validation checklist

We validated the implementation by checking that:

1. the API key is server-side only,
2. model discovery is startup-based and cached,
3. candidate models are ordered and filtered,
4. the generation path retries and falls back,
5. `json_schema` is used instead of deprecated `json_mode`,
6. `thinking_budget=0` is applied where needed,
7. `/health` reports meaningful readiness data,
8. failing provider requests produce safe `503` responses instead of misleading server errors.

This is the standard pattern we now use for Gemini-backed services in AkadVerse.
