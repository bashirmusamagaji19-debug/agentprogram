from web_task_agent.models import MatchResult
from web_task_agent.skill_gap import summarize_skill_gaps


def test_summarize_skill_gaps_counts_and_sorts_missing_skills() -> None:
    matches = [
        MatchResult(job_id="1", score=0.5, missing_skills=["LLM", "FastAPI"]),
        MatchResult(job_id="2", score=0.4, missing_skills=["LLM", "SQL"]),
        MatchResult(job_id="3", score=1.0, missing_skills=[]),
    ]

    gaps = summarize_skill_gaps(matches)

    assert gaps == [("LLM", 2), ("FastAPI", 1), ("SQL", 1)]


def test_summarize_skill_gaps_normalizes_case_and_whitespace() -> None:
    matches = [
        MatchResult(job_id="1", score=0.5, missing_skills=[" LLM ", "llm"]),
        MatchResult(job_id="2", score=0.4, missing_skills=["FastAPI"]),
    ]

    gaps = summarize_skill_gaps(matches)

    assert gaps == [("LLM", 2), ("FastAPI", 1)]
