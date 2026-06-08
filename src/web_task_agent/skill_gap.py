from __future__ import annotations

from collections import Counter

from web_task_agent.models import MatchResult


def summarize_skill_gaps(matches: list[MatchResult]) -> list[tuple[str, int]]:
    display_by_key: dict[str, str] = {}
    counts: Counter[str] = Counter()
    for match in matches:
        for skill in match.missing_skills:
            cleaned = skill.strip()
            if not cleaned:
                continue
            key = cleaned.casefold()
            display_by_key.setdefault(key, cleaned)
            counts[key] += 1
    return [
        (display_by_key[key], count)
        for key, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], display_by_key[item[0]].casefold()),
        )
    ]
