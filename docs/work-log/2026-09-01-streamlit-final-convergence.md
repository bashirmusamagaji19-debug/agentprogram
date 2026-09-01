# Work Log — 2026-09-01 Streamlit 最终收敛

> 分支：`feature/chinese-job-pipeline`  
> 设计：`docs/superpowers/specs/2026-09-01-streamlit-final-convergence-design.md`

## 目标

在不复制现有 Agent 业务逻辑的前提下，增加可本地运行、可部署为单实例云端演示的 Streamlit 交互入口。

## 已实现

- `streamlit_runner.py`：类型化 UI 请求、三种数据模式校验、provider 环境变量存在性检查、现有 LangGraph workflow 调用、独立运行目录和下载白名单。
- `streamlit_app.py`：岗位条件、简历文本/文件、模式与模型设置、岗位结果、失败诊断、执行轨迹和四类产物下载。
- 根目录稳定入口与 `.streamlit/config.toml`。
- `streamlit-runs/` 加入 Git 忽略，不提交用户输入、SQLite 或运行产物。

## 安全与边界

- 页面不接受 API key 输入，只检查 `DASHSCOPE_API_KEY` / `DEEPSEEK_API_KEY` 是否存在。
- 聚合上传文件仅在一次任务期间落到临时路径，完成后删除。
- 每次运行使用独立目录，下载函数只能读取本次结果登记的产物。
- 云端属于单实例演示：本地文件和 SQLite 不保证持久，不支持跨实例共享。
- 本轮没有使用真实简历，因此不新增真实求职效果声明。

## 验证记录

- 全量 pytest：`422 passed`。
- 总覆盖率：`89.83%`，高于 `70%` 门槛。
- `--release-check`：focused Ruff、pytest/coverage、wheel-build、doctor、strict HITL、git-diff-check 六阶段全部通过。
- 独立 `pip wheel . --no-deps`：成功生成 `web_task_agent-0.1.0-py3-none-any.whl`。
- Streamlit 健康端点：`http://127.0.0.1:8501/_stcore/health` 返回 `ok`。
- 桌面浏览器：Demo 运行得到 3 个有效岗位；四列指标、岗位表、诊断、轨迹和四个下载按钮可达；页面无横向溢出。
- 移动浏览器（390×844）：发现四列指标不换行后按 TDD 修复为两列响应式网格；复验计算样式为两列 `168px 168px`，页面 `scrollWidth == clientWidth == 390`。
- Streamlit AppTest：数据模式可从 Demo 切换为指定 URL，切换后出现 URL 输入区域，无脚本异常。

未验证项：真实 provider、真实简历和公开云端 URL 本轮未执行，因此不计入完成证据。
