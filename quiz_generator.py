"""
AkadVerse - Quiz Generator
Tier 5 | Microservice Port: 8016
========================================================================
v2.0 - Robust Architecture & Creative Pedagogical Upgrades

What changed:
  - Upgraded model discovery to use the unified google-genai SDK.
  - Implemented the FastAPI lifespan manager for database initialization.
  - Added a context manager for SQLite to prevent connection leaks.
  - Revamped the prompt template to enforce Nigerian university standards,
    real-world analogies, and strict distractors for common misconceptions.
"""

import sqlite3
import json
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager, contextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from typing import Any, List, Optional, AsyncIterator
from dotenv import load_dotenv

# LangChain and Unified Google GenAI imports
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import PromptTemplate
from google import genai

load_dotenv()

logger = logging.getLogger(__name__)

# =========================================================
# 1. Pydantic Models for Structured Output and API Requests
# =========================================================

class Question(BaseModel):
    """Schema for a single generated quiz question."""
    question_text: str = Field(description="The actual question being asked.")
    question_type: str = Field(description="Type of question: MCQ, short-answer, or essay.")
    options: Optional[List[str]] = Field(default=None, description="List of 4 options if it is an MCQ. Must include plausible distractors.")
    correct_answer: str = Field(description="The correct answer or model answer.")
    explanation: str = Field(description="Detailed explanation of why the answer is correct and why distractors are wrong.")

class Quiz(BaseModel):
    """Schema for the entire quiz output."""
    topic: str = Field(description="The topic of the quiz.")
    difficulty: str = Field(description="The difficulty level.")
    questions: List[Question] = Field(description="List of generated questions.")

class QuizRequest(BaseModel):
    """Schema for the incoming API request from the user."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=500)
    difficulty: str = Field(min_length=1, max_length=100)
    question_count: int = Field(default=3, ge=1, le=20)


class ToolEnvelope(BaseModel):
    """Common request envelope sent by the orchestrator to tools."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    params: dict[str, Any]

# =========================================================
# 2. Database Connection Management
# =========================================================

DB_PATH = "akadverse_question_bank.db"
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
MODEL_DISCOVERY_TTL_SECONDS = int(os.getenv("MODEL_DISCOVERY_TTL_SECONDS", "300"))
MODEL_REQUEST_TIMEOUT_SECONDS = float(os.getenv("MODEL_REQUEST_TIMEOUT_SECONDS", "45"))
MODEL_RETRY_ATTEMPTS = int(os.getenv("MODEL_RETRY_ATTEMPTS", "2"))
MODEL_RETRY_BACKOFF_SECONDS = float(os.getenv("MODEL_RETRY_BACKOFF_SECONDS", "1"))
MODEL_PRIORITY = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-3.1-pro-preview",
    "gemini-2.5-pro",
]
MODEL_CANDIDATES: list[str] = []
MODEL_DISCOVERY_EXPIRES_AT = 0.0
SELECTED_MODEL: Optional[str] = None
LAST_ATTEMPTED_MODEL: Optional[str] = None
LAST_SUCCESSFUL_MODEL: Optional[str] = None
LAST_MODEL_ERROR: Optional[str] = None
FALLBACK_COUNT = 0

@contextmanager
def get_db():
    """
    Context manager for SQLite connections.
    Guarantees the connection is always closed, preventing leaks even on exceptions.
    """
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        yield conn
    except sqlite3.Error as e:
        if conn:
            conn.rollback()
        logger.exception("Database operation failed")
        raise
    finally:
        if conn:
            conn.close()

def init_db():
    """Initializes the local SQLite database for the question bank."""
    try:
        with get_db() as conn:
            # Create a table organized by course/topic and difficulty
            conn.execute('''
                CREATE TABLE IF NOT EXISTS question_bank (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL,
                    difficulty TEXT NOT NULL,
                    question_data TEXT NOT NULL
                )
            ''')
            conn.commit()
        logger.info("Question bank database initialized successfully")
    except sqlite3.Error as e:
        logger.exception("Database initialization failed")
        raise

# =========================================================
# 3. Dynamic Model Discovery Logic
# =========================================================

def discover_model_candidates(api_key_str: str) -> list[str]:
    """
    Discover an ordered list of available text-generation models.
    """
    if not api_key_str:
        return []

    try:
        client = genai.Client(api_key=api_key_str)
        
        all_models = [
            m.name.replace("models/", "")
            for m in client.models.list() 
            if m.name and _is_text_model(m.name.replace("models/", ""))
        ]
        
        prioritized = [model for model in MODEL_PRIORITY if model in all_models]
        discovered_other_models = [model for model in all_models if model not in prioritized]
        return prioritized + discovered_other_models
    except Exception as e:
        logger.warning("Model discovery failed: %s", e)
        return []
    

def _is_text_model(model_name: str) -> bool:
    """Keep only Gemini models intended for text generation."""
    normalized = model_name.lower()
    blocked_tokens = ("embedding", "image", "vision", "audio", "speech", "transcribe", "tts")
    return normalized.startswith("gemini") and not any(token in normalized for token in blocked_tokens)


async def get_model_candidates() -> list[str]:
    """Return cached model candidates, refreshing discovery when the cache expires."""
    global MODEL_CANDIDATES, MODEL_DISCOVERY_EXPIRES_AT

    now = time.monotonic()
    if MODEL_CANDIDATES and MODEL_DISCOVERY_EXPIRES_AT > now:
        return MODEL_CANDIDATES

    MODEL_CANDIDATES = await asyncio.to_thread(discover_model_candidates, GOOGLE_API_KEY)
    MODEL_DISCOVERY_EXPIRES_AT = now + MODEL_DISCOVERY_TTL_SECONDS
    return MODEL_CANDIDATES


def is_transient_model_error(error: Exception) -> bool:
    """Identify provider failures that may succeed on retry or another model."""
    message = str(error).lower()
    transient_markers = (
        "429", "resource_exhausted", "rate limit", "quota", "503", "unavailable",
        "timeout", "timed out", "connection reset", "temporarily",
    )
    return any(marker in message for marker in transient_markers)


async def generate_quiz_with_fallback(prompt: str, candidates: list[str]) -> Quiz:
    """Generate a quiz with bounded retries and model fallback."""
    global SELECTED_MODEL, LAST_ATTEMPTED_MODEL, LAST_SUCCESSFUL_MODEL
    global LAST_MODEL_ERROR, FALLBACK_COUNT

    if not candidates:
        raise HTTPException(status_code=503, detail="No Gemini models are currently available")

    last_error: Optional[Exception] = None
    for model_index, model_name in enumerate(candidates):
        for attempt in range(MODEL_RETRY_ATTEMPTS):
            LAST_ATTEMPTED_MODEL = model_name
            SELECTED_MODEL = model_name
            logger.info("Calling Gemini model %s (attempt %s/%s)", model_name, attempt + 1, MODEL_RETRY_ATTEMPTS)
            try:
                llm = ChatGoogleGenerativeAI(
                    model=model_name,
                    api_key=GOOGLE_API_KEY,
                    temperature=0.7,
                    # Explicitly disable thinking to prevent the structured output hang
                    thinking_budget=0,
                )
                # Use the updated json_schema method
                structured_llm = llm.with_structured_output(Quiz, method="json_schema") 
                result = await asyncio.wait_for(
                    structured_llm.ainvoke(prompt),
                    timeout=MODEL_REQUEST_TIMEOUT_SECONDS,
                )
                parsed_result = result if isinstance(result, Quiz) else Quiz.model_validate(result)
                LAST_SUCCESSFUL_MODEL = model_name
                LAST_MODEL_ERROR = None
                return parsed_result
            except Exception as error:
                last_error = error
                LAST_MODEL_ERROR = f"{type(error).__name__}: {error}"
                if not is_transient_model_error(error):
                    raise
                if attempt + 1 < MODEL_RETRY_ATTEMPTS:
                    await asyncio.sleep(MODEL_RETRY_BACKOFF_SECONDS * (2 ** attempt))

        if model_index < len(candidates) - 1:
            FALLBACK_COUNT += 1
            logger.warning("Model %s exhausted; falling back to %s", model_name, candidates[model_index + 1])

    raise HTTPException(
        status_code=503,
        detail="All discovered Gemini models are temporarily unavailable. Please try again later.",
    ) from last_error

# =========================================================
# 4. FastAPI Application & Lifespan Setup
# =========================================================

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Manages startup and shutdown events, including DB init."""
    logger.info("AkadVerse Quiz Generator initializing")
    init_db()
    candidates = await get_model_candidates()
    if candidates:
        logger.info("Discovered %s Gemini text-generation model(s)", len(candidates))
    elif GOOGLE_API_KEY:
        logger.warning("Gemini model discovery returned no usable candidates")
    else:
        logger.warning("GOOGLE_API_KEY is not configured; model discovery skipped")
    logger.info("Quiz Generator ready on port 8016")
    yield
    logger.info("AkadVerse Quiz Generator stopped")

app = FastAPI(
    title="AkadVerse Quiz Generator API",
    description="Tier 5 E-Learning Sub-Agent for generating creative, university-standard quizzes.",
    version="2.0",
    lifespan=lifespan
)

# A highly creative, pedagogically sound prompt template
prompt_template = PromptTemplate(
    template="""You are a rigorous, highly creative university professor at a top-tier Nigerian university.
    Your task is to generate an engaging, academically challenging quiz on the following topic.
    
    Topic: {topic}
    Difficulty Level: {difficulty}
    Number of questions: {question_count}
    
    CREATIVE AND PEDAGOGICAL GUIDELINES:
    1. Real-World Relevance: Embed at least one question in a relatable, real-world scenario or analogy (e.g., relating algorithms to Lagos traffic or business logic).
    2. Academic Rigor: Ensure the depth matches strict 300-level university examinations.
    3. The Distractor Rule: If the question_type is MCQ, you MUST provide exactly 4 options. The incorrect options (distractors) must not be random; they must specifically target common student misconceptions.
    4. Rich Explanations: The explanation field must not just state the right answer. It must teach the student *why* the answer is correct and briefly explain why the main distractors are wrong.
    
    Return the output strictly in the requested JSON format.
    """,
    input_variables=["topic", "difficulty", "question_count"]
)

# =========================================================
# 5. API Endpoints
# =========================================================

@app.get("/health", tags=["Health"])
def health():
    database_status = "ok"
    try:
        with get_db() as conn:
            conn.execute("SELECT 1")
    except sqlite3.Error:
        database_status = "error"

    is_ready = bool(GOOGLE_API_KEY and MODEL_CANDIDATES and database_status == "ok")
    return {
        "status": "ready" if is_ready else "not_ready",
        "service": "AkadVerse Quiz Generator",
        "llm": "google-gemini",
        "gemini_configured": bool(GOOGLE_API_KEY),
        "model_discovery": bool(MODEL_CANDIDATES),
        "candidate_models": MODEL_CANDIDATES,
        "selected_model": SELECTED_MODEL,
        "last_attempted_model": LAST_ATTEMPTED_MODEL,
        "last_successful_model": LAST_SUCCESSFUL_MODEL,
        "fallback_count": FALLBACK_COUNT,
        "last_model_error": LAST_MODEL_ERROR,
        "database_status": database_status,
        "database": "sqlite",
        "database_path": DB_PATH,
    }


async def generate_quiz_for_request(request: QuizRequest) -> Quiz:
    """Generate, persist, and return one validated quiz request."""
    if not GOOGLE_API_KEY:
        raise HTTPException(status_code=503, detail="GOOGLE_API_KEY is not configured on the server")

    try:
        # Step 1: Discover candidates and format the prompt.
        candidates = await get_model_candidates()
        _input = prompt_template.format_prompt(
            topic=request.topic,
            difficulty=request.difficulty,
            question_count=request.question_count
        )

        # Step 2: Generate with bounded retries and model fallback.
        parsed_quiz = await generate_quiz_with_fallback(_input.to_string(), candidates)

        # Step 3: Safe database insertion using the context manager.
        with get_db() as conn:
            conn.execute(
                "INSERT INTO question_bank (topic, difficulty, question_data) VALUES (?, ?, ?)",
                (parsed_quiz.topic, parsed_quiz.difficulty, parsed_quiz.model_dump_json())
            )
            conn.commit()
        
        # Step 4: Kafka/Redpanda mock event.
        logger.info("Published assessment.generated event for topic: %s", parsed_quiz.topic)
        
        return parsed_quiz

    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Quiz generation failed")
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred while generating the quiz.",
        ) from error


@app.post("/generate-quiz", response_model=Quiz, tags=["Generation"])
async def generate_quiz(request: QuizRequest) -> Quiz:
    """Generate a quiz from the direct service request format."""
    return await generate_quiz_for_request(request)


@app.post("/chat/contextual", response_model=Quiz, tags=["Generation"])
async def contextual_quiz(envelope: ToolEnvelope) -> Quiz:
    """Generate a quiz from the standard orchestrator tool envelope."""
    if envelope.tool_name != "quiz_generator":
        raise HTTPException(status_code=400, detail="Invalid tool name")

    try:
        request = QuizRequest.model_validate(envelope.params)
    except ValidationError as error:
        logger.warning(
            "Invalid quiz tool parameters for session %s: %s",
            envelope.session_id,
            error.errors(),
        )
        raise HTTPException(status_code=422, detail=error.errors()) from error

    return await generate_quiz_for_request(request)

# =========================================================
# Run: uvicorn quiz_generator:app --host 127.0.0.1 --port 8016 --reload
# =========================================================