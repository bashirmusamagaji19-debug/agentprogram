from web_task_agent.open_search.query_parser import DemoQueryParser


def test_demo_parser_extracts_location_skills_and_exclusions():
    intent = DemoQueryParser().parse(
        "找北京或远程 Agent 实习，要求 Python、LangGraph，排除产品经理"
    )
    assert intent.locations == ["北京", "远程"]
    assert "Python" in intent.required_skills
    assert "LangGraph" in intent.required_skills
    assert "产品经理" in intent.excluded_roles
