"""中文 JD fixture 页面（阶段 2 抽取中文化测试用）。

两种主流版式：
- LABELED：牛客风格"标签行+全角冒号"，规则标签解析应命中
- UNSTRUCTURED：无标签整段式，规则 section marker 应命中
"""

from __future__ import annotations

from web_task_agent.models import BrowserPage

# 牛客风格：标签行 + 全角冒号
CHINESE_LABELED_PAGE = BrowserPage(
    url="https://www.nowcoder.com/jobs/detail/999001",
    title="大模型算法工程师【2027届】_示例科技-牛客网",
    content=(
        "职位名称：大模型算法工程师（实习生）\n"
        "公司名称：示例科技有限公司\n"
        "工作地点：北京市\n"
        "发布时间：2026-08-30\n"
        "岗位职责：\n"
        "1. 参与大模型后训练与对齐研发，覆盖数据处理、SFT及强化学习等环节；\n"
        "2. 跟踪并攻关Long Context、Agent-RL等前沿方向。\n"
        "任职要求：\n"
        "1. 扎实的机器学习基础，熟悉LLM与PyTorch；\n"
        "2. 熟悉大模型训练全流程（Pretrain/SFT/RL）者优先。\n"
    ),
    source="nowcoder-fixture",
)

# 无标签整段式（企业官网常见的 server-rendered 页面）
CHINESE_UNSTRUCTURED_PAGE = BrowserPage(
    url="https://careers.example.com/cn/job/999002",
    title="感知算法实习生-示例自动驾驶",
    content=(
        "示例自动驾驶科技有限公司\n"
        "上海市·浦东新区\n"
        "岗位职责\n"
        "深入分析路测感知问题，定位根因并设计数据方案；参与2D/3D检测模型的轻量化。\n"
        "任职要求\n"
        "计算机/机器人等相关专业；精通Python与PyTorch；熟悉BEV感知者优先。\n"
        "福利待遇\n"
        "六险一金，免费三餐。\n"
    ),
    source="official-site-fixture",
)
