from web_task_agent.open_search.evidence import build_content_hash, build_field_evidence


def test_field_evidence_hash_is_stable():
    evidence = build_field_evidence("title", "Agent Intern", source_text="...")
    assert evidence.content_hash == build_content_hash("...")
