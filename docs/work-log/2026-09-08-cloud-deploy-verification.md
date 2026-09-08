# Work Log — 2026-09-08 Streamlit Community Cloud 部署验证

> 分支:`feature/chinese-job-pipeline`
> 前置:`2026-09-01-streamlit-final-convergence.md`(遗留项"公开云端 URL 未验证")

## 目标

把 Streamlit 入口真正部署到 Streamlit Community Cloud,拿到公开 URL 并完成验证;诚实记录平台边界。

## 已实现

- `requirements.txt`(仓库根):云端最小依赖(streamlit / langgraph / langgraph-checkpoint-sqlite / pydantic / python-dotenv),**不含 browser-use**——浏览器路径在 `browser.py` 为懒加载,Demo / 聚合 JSON / HTTP 抓取与 LLM 抽取、匹配不依赖它。注释保持 ASCII(#30)。
- 根入口 `streamlit_app.py`:`sys.path` 注入 `src/`,适配云端无 `pip install -e .` 的环境。
- `sync_provider_secrets()`(`streamlit_runner.py`):应用启动时把 Streamlit Secrets 的 provider key 补进进程环境(环境变量优先、不覆盖),解决 `st.secrets` 不自动注入 `os.environ` 而 `build_configured_llm_*` 直读 `os.environ` 的分层裂缝(#31)。
- TDD 5 项新测试;全量 pytest `427 passed`。

## 验证记录

- 本地仿真云端冷启动:干净 venv 只装 `requirements.txt`(无 browser-use/playwright)→ headless 启动 health `ok`、页面 200;Demo 管线 3 valid jobs、0 失败、四类产物(JSON/Markdown/HTML Dashboard/行动计划)全部落盘。
- 推送:`feature/chinese-job-pipeline` 推到 GitHub(含 3 个新提交:`d417c27` 云端准备、`25f17ce` ASCII 修正、`e9ce95b` 调试故事 #30-#31)。
- 云端部署:share.streamlit.io,仓库 `bashirmusamagaji19-debug/agentprogram`,分支 `feature/chinese-job-pipeline`,入口 `streamlit_app.py`,Python 3.12。
- 公开可见性:Settings → Sharing → "This app is public and searchable" + Save;**用户无痕窗口(无登录态)确认可直接打开**。
- Demo 模式:用户在云端页面运行内置 Demo 正常。

## 过程教训(详见 `docs/interview-debugging-stories.md` #30-#32)

- pip 按 locale(Windows GBK)读 requirements 文件,UTF-8 中文注释直接解码失败——跨机器分发的配置文件保持 ASCII。
- Streamlit Secrets ≠ 环境变量;凭据注入点要选在"直读全局状态"那层的上游。
- **health 端点匿名 303 不是私有信号**:Streamlit 边缘对无浏览器会话的请求(含不存在的子域名)统一 303 到登录页——连续三轮"修配置"全是无效功,直到对照组实验(不存在的子域名同样 303)推翻验证假设本身。平台类黑盒的探测手段要先做对照校准。

## 未验证项 / 诚实边界

- LLM(qwen)模式未在云端实测(用户未配 Secrets);配置路径已由单测覆盖,配置后即可用。
- 云端出口 IP 对国内招聘站的可达性未测;云端演示以 Demo / 聚合 JSON / 上传模式为主,不依赖实时爬站。
- health 端点的外部自动化监控不可行(平台门禁),公开验证以真实浏览器无痕窗口为准。
- `streamlit-runs/` 与 SQLite 在云端为单实例临时状态,重启即失(README 既有声明,不变)。

## 云端 URL

https://agentprogram-lpfrmgsybu89iaffxyzc5f.streamlit.app/
