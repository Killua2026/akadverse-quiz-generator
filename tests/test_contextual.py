from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import quiz_generator


def test_contextual_quiz_validates_and_forwards_parameters(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_generate(request: quiz_generator.QuizRequest) -> quiz_generator.Quiz:
        captured["request"] = request
        return quiz_generator.Quiz(
            topic=request.topic,
            difficulty=request.difficulty,
            questions=[],
        )

    monkeypatch.setattr(quiz_generator, "generate_quiz_for_request", fake_generate)

    result = asyncio.run(
        quiz_generator.contextual_quiz(
            quiz_generator.ToolEnvelope(
                session_id="session-123",
                tool_name="quiz_generator",
                params={
                    "topic": "Photosynthesis",
                    "difficulty": "medium",
                    "question_count": 5,
                },
            )
        )
    )

    request = captured["request"]
    assert isinstance(request, quiz_generator.QuizRequest)
    assert request.topic == "Photosynthesis"
    assert request.difficulty == "medium"
    assert request.question_count == 5
    assert result.topic == "Photosynthesis"


def test_contextual_quiz_rejects_unknown_tool() -> None:
    with pytest.raises(HTTPException) as error_info:
        asyncio.run(
            quiz_generator.contextual_quiz(
                quiz_generator.ToolEnvelope(
                    session_id="session-123",
                    tool_name="notes_creator",
                    params={
                        "topic": "Photosynthesis",
                        "difficulty": "medium",
                        "question_count": 5,
                    },
                )
            )
        )

    assert error_info.value.status_code == 400


def test_contextual_quiz_returns_unprocessable_entity_for_invalid_params() -> None:
    with pytest.raises(HTTPException) as error_info:
        asyncio.run(
            quiz_generator.contextual_quiz(
                quiz_generator.ToolEnvelope(
                    session_id="session-123",
                    tool_name="quiz_generator",
                    params={
                        "topic": "Photosynthesis",
                        "question_count": 0,
                    },
                )
            )
        )

    assert error_info.value.status_code == 422