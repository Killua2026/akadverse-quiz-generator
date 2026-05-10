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
from contextlib import asynccontextmanager, contextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, AsyncIterator

# LangChain and Unified Google GenAI imports
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import PromptTemplate
from google import genai

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
    topic: str
    difficulty: str
    question_count: int = 3
    google_api_key: str = Field(description="Your Google Gemini API key.")

# =========================================================
# 2. Database Connection Management
# =========================================================

DB_PATH = "akadverse_question_bank.db"

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
        print(f"[DB ERROR] {e}")
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
        print("[DB] Question bank database initialized successfully.")
    except sqlite3.Error as e:
        print(f"[DB ERROR] Database initialization failed: {e}")
        raise

# =========================================================
# 3. Dynamic Model Discovery Logic
# =========================================================

def get_valid_model_name(api_key_str: str) -> str:
    """
    Dynamically finds a working Google Gemini model to avoid 404 errors.
    Uses the modern unified google-genai SDK instance.
    """
    try:
        client = genai.Client(api_key=api_key_str)
        
        all_models = [
            m.name.replace("models/", "") 
            for m in client.models.list() 
            if m.name
        ]
        
        priority_order = [
            "gemini-2.5-flash", "gemini-2.0-flash", 
            "gemini-1.5-flash", "gemini-pro"
        ]
        
        for preferred in priority_order:
            if preferred in all_models:
                print(f"[Model] Selected AI Model dynamically: {preferred}")
                return preferred
                
        if all_models:
            print(f"[Model] Falling back to first available model: {all_models[0]}")
            return all_models[0]
            
    except Exception as e:
        print(f"[Model WARNING] Discovery failed ({e}). Defaulting to 'gemini-1.5-flash'.")
    
    return "gemini-1.5-flash"

# =========================================================
# 4. FastAPI Application & Lifespan Setup
# =========================================================

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Manages startup and shutdown events, including DB init."""
    print("[Startup] AkadVerse Quiz Generator initializing...")
    init_db()
    print("[Startup] Ready. Run with: uvicorn quiz_generator:app --host 127.0.0.1 --port 8016 --reload")
    yield
    print("[Shutdown] AkadVerse Quiz Generator stopped.")

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

@app.post("/generate-quiz", response_model=Quiz, tags=["Generation"])
async def generate_quiz(request: QuizRequest):
    """Endpoint to generate a new creative quiz and save it to the question bank."""
    try:
        # Step 1: Run dynamic model discovery
        selected_model = get_valid_model_name(request.google_api_key)
        
        # Step 2: Initialize LangChain with the selected model
        llm = ChatGoogleGenerativeAI(
            model=selected_model,
            api_key=request.google_api_key,
            temperature=0.7 # High enough for creativity, low enough for structure
        )
        
        # Step 3: Enforce strict Pydantic output
        structured_llm = llm.with_structured_output(Quiz, method="json_mode")
        
        # Step 4: Format the creative prompt
        _input = prompt_template.format_prompt(
            topic=request.topic,
            difficulty=request.difficulty,
            question_count=request.question_count
        )
        
        # Step 5: Generate the quiz
        print(f"[Generate] Calling Gemini API using {selected_model} for topic: {request.topic}...")
        parsed_quiz: Quiz = structured_llm.invoke(_input.to_string()) # type: ignore
        
        # Step 6: Safe database insertion using the context manager
        with get_db() as conn:
            conn.execute(
                "INSERT INTO question_bank (topic, difficulty, question_data) VALUES (?, ?, ?)",
                (parsed_quiz.topic, parsed_quiz.difficulty, parsed_quiz.model_dump_json())
            )
            conn.commit()
        
        # Step 7: Kafka mock event
        print(f"[KAFKA MOCK] Published event 'assessment.generated' for topic: {parsed_quiz.topic}")
        
        return parsed_quiz

    except Exception as e:
        print(f"[Pipeline ERROR] Quiz generation failed: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"An error occurred while generating the quiz: {str(e)}"
        )

# =========================================================
# Run: uvicorn quiz_generator:app --host 127.0.0.1 --port 8016 --reload
# =========================================================