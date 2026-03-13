import sqlite3
import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional

# LangChain and Google Generative AI imports
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import PromptTemplate
import google.generativeai as genai

# ---------------------------------------------------------
# 1. Pydantic Models for Structured Output and API Requests
# ---------------------------------------------------------

class Question(BaseModel):
    """Schema for a single generated quiz question."""
    question_text: str = Field(description="The actual question being asked.")
    question_type: str = Field(description="Type of question: MCQ, short-answer, or essay.")
    options: Optional[List[str]] = Field(default=None, description="List of options if it is an MCQ. Must include distractors.")
    correct_answer: str = Field(description="The correct answer or model answer.")
    explanation: str = Field(description="Explanation of why the answer is correct.")

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
    google_api_key: str = "YOUR_GEMINI_API_KEY"

# ---------------------------------------------------------
# 2. Database Initialization (SQLite simulating PostgreSQL)
# ---------------------------------------------------------

def init_db():
    """Initializes the local SQLite database for the question bank."""
    try:
        conn = sqlite3.connect("akadverse_question_bank.db")
        cursor = conn.cursor()
        # Create a table organized by course/topic and difficulty
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS question_bank (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                difficulty TEXT NOT NULL,
                question_data TEXT NOT NULL
            )
        ''')
        conn.commit()
    except sqlite3.Error as e:
        print(f"Database error during initialization: {e}")
    finally:
        if conn:
            conn.close()

# Initialize the database on startup
init_db()

# ---------------------------------------------------------
# 3. Dynamic Model Discovery Logic
# ---------------------------------------------------------

def get_valid_model_name(api_key: str) -> str:
    """
    Dynamically finds a working Google Gemini model to avoid 404 errors.
    Queries the API to see which models are currently available and active.
    """
    try:
        # Configure the Google SDK with the user's API key
        genai.configure(api_key=api_key)  # type: ignore
        
        # Ask Google which models are available and support generation
        all_models = [m.name.replace("models/", "") for m in genai.list_models()  # type: ignore 
                      if "generateContent" in m.supported_generation_methods]
        
        # Priority list (preferred models first)
        priority_order = [
            "gemini-2.5-flash", "gemini-1.5-flash", "gemini-2.0-flash",
            "gemini-pro", "gemini-1.0-pro"
        ]
        
        # Check priority list against available models
        for preferred in priority_order:
            if preferred in all_models:
                print(f"[System]: Selected AI Model dynamically: {preferred}")
                return preferred
        
        # Fallback: take the first available one if priorities fail
        if all_models:
            print(f"[System]: Falling back to first available model: {all_models[0]}")
            return all_models[0]
            
    except Exception as e:
        # Catch network or authentication errors during discovery
        print(f"[System Warning]: Model discovery failed ({e}). Defaulting to 'gemini-1.5-flash'.")
    
    # Hard fallback if the dynamic discovery completely fails
    return "gemini-1.5-flash"

# ---------------------------------------------------------
# 4. FastAPI Application Setup
# ---------------------------------------------------------

app = FastAPI(
    title="AkadVerse Quiz Generator API",
    description="Tier 5 E-Learning Sub-Agent for generating educational quizzes with dynamic model selection.",
    version="1.1"
)

# Create a prompt template instructing the LLM
prompt_template = PromptTemplate(
    template="""You are an expert university professor. Generate a quiz on the following topic.
    Topic: {topic}
    Difficulty: {difficulty}
    Number of questions: {question_count}
    
    Ensure the questions are highly accurate, academic, and if multiple choice, include plausible distractors.
    """,
    input_variables=["topic", "difficulty", "question_count"]
)

# ---------------------------------------------------------
# 5. API Endpoints
# ---------------------------------------------------------

@app.post("/generate-quiz", response_model=Quiz)
async def generate_quiz(request: QuizRequest):
    """Endpoint to generate a new quiz and save it to the question bank."""
    try:
        # Step 1: Run the dynamic model discovery to prevent 404 errors
        selected_model = get_valid_model_name(request.google_api_key)
        
        # Step 2: Initialize the LangChain Google model with the dynamically selected name
        llm = ChatGoogleGenerativeAI(
            model=selected_model,
            google_api_key=request.google_api_key,
            temperature=0.7
        )
        
        # Step 3: Use LangChain's native structured output feature
        structured_llm = llm.with_structured_output(Quiz, method="json_mode")  # type: ignore[call-arg]
        
        # Step 4: Format the prompt
        _input = prompt_template.format_prompt(
            topic=request.topic,
            difficulty=request.difficulty,
            question_count=request.question_count
        )
        
        # Step 5: Call the LLM
        print(f"Calling Gemini API using model: {selected_model}...")
        parsed_quiz: Quiz = structured_llm.invoke(_input.to_string())  # type: ignore[assignment]
        
        # Step 6: Save the generated quiz to our local SQLite question bank
        conn = sqlite3.connect("akadverse_question_bank.db")
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO question_bank (topic, difficulty, question_data) VALUES (?, ?, ?)",
            (parsed_quiz.topic, parsed_quiz.difficulty, parsed_quiz.model_dump_json())  # type: ignore
        )
        conn.commit()
        conn.close()
        
        # Step 7: Simulate the Kafka event publishing
        print(f"[KAFKA MOCK] Published event 'assessment.generated' for topic: {parsed_quiz.topic}")
        
        return parsed_quiz

    except Exception as e:
        # Robust error handling for the entire pipeline
        raise HTTPException(
            status_code=500, 
            detail=f"An error occurred while generating the quiz: {str(e)}"
        )

# Run instructions: uvicorn quiz_generator:app --host 127.0.0.1 --port 8005 --reload