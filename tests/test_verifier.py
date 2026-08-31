from web_task_agent.models import JobPosting
from web_task_agent.verifier import JobVerifier


def make_job(**overrides):
    data = {
        "title": "AI Engineering Intern",
        "company": "Example AI",
        "location": "Remote",
        "source": "fixture",
        "url": "https://example.com/jobs/1",
        "requirements": "Python, LangGraph, LLM",
        "responsibilities": "Build AI agents",
        "skills": ["Python", "LangGraph", "LLM"],
        "confidence": 0.9,
    }
    data.update(overrides)
    return JobPosting(**data)


def test_verifier_accepts_relevant_complete_job():
    verifier = JobVerifier(required_keywords=["AI", "LLM", "Agent"])

    result = verifier.verify(make_job())

    assert result.is_valid is True
    assert result.reasons == []


def test_verifier_rejects_low_confidence_job():
    verifier = JobVerifier(required_keywords=["AI"])

    result = verifier.verify(make_job(confidence=0.3))

    assert result.is_valid is False
    assert "confidence below 0.5" in result.reasons


def test_verifier_rejects_missing_requirements_and_responsibilities():
    verifier = JobVerifier(required_keywords=["AI"])

    result = verifier.verify(make_job(requirements="", responsibilities=""))

    assert result.is_valid is False
    assert "missing requirements and responsibilities" in result.reasons


def test_verifier_rejects_irrelevant_job_case_insensitively():
    verifier = JobVerifier(required_keywords=["LLM", "Agent"])

    result = verifier.verify(
        make_job(
            title="Backend Intern",
            requirements="Java, Spring",
            responsibilities="Build payment services",
            skills=["Java"],
        )
    )

    assert result.is_valid is False
    assert "not relevant to AI job direction" in result.reasons


def test_default_keywords_accept_valid_chinese_ai_job():
    """阶段 1 实测被英文关键词误杀的中文岗位，默认双语词表下必须通过。"""
    verifier = JobVerifier()

    result = verifier.verify(
        make_job(
            title="大模型算法工程师（实习生）",
            company="美团",
            location="北京市",
            requirements="1、扎实的算法基础，熟悉LLM；2、熟悉 PyTorch 等主流深度学习框架。",
            responsibilities="1、参与大模型后训练与对齐工作；2、优化 Agent 任务规划能力。",
            skills=["Python", "大模型", "PyTorch"],
        )
    )

    assert result.is_valid is True
    assert result.reasons == []


def test_default_keywords_match_chinese_title_without_english_terms():
    """正文完全无英文 AI 词，仅凭中文关键词（算法/大模型）也应判定相关。"""
    verifier = JobVerifier()

    result = verifier.verify(
        make_job(
            title="感知算法实习生",
            company="某自动驾驶公司",
            location="深圳",
            requirements="精通Python与PyTorch，熟悉2D/3D检测。",
            responsibilities="分析路测感知问题，设计数据方案。",
            skills=["Python", "PyTorch"],
        )
    )

    assert result.is_valid is True


def test_default_keywords_still_reject_irrelevant_chinese_job():
    """既不含英文 AI 词也不含中文 AI 词的岗位仍被拦截。"""
    verifier = JobVerifier()

    result = verifier.verify(
        make_job(
            title="行政实习生",
            company="某公司",
            location="北京",
            requirements="熟练使用Office办公软件。",
            responsibilities="协助处理日常行政事务。",
            skills=["Office"],
        )
    )

    assert result.is_valid is False
    assert "not relevant to AI job direction" in result.reasons


def test_dedupe_removes_same_company_title_pair():
    verifier = JobVerifier(required_keywords=["AI"])
    first = make_job(url="https://example.com/jobs/1")
    duplicate = make_job(url="https://example.com/jobs/2")

    unique, duplicates = verifier.dedupe([first, duplicate])

    assert unique == [first]
    assert duplicates == [duplicate]


def test_dedupe_removes_exact_duplicate_url_before_title_pair():
    verifier = JobVerifier(required_keywords=["AI"])
    first = make_job(url="https://example.com/jobs/1")
    duplicate = make_job(
        title="Different AI Intern",
        company="Other AI",
        url="https://example.com/jobs/1",
    )

    unique, duplicates = verifier.dedupe([first, duplicate])

    assert unique == [first]
    assert duplicates == [duplicate]
