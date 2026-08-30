# 公开演示部署检查清单

> 状态说明：仓库、测试、Docker 构建和本地 HTTP smoke 已完成；云平台账号授权、公网 URL 和真实 Tavily 搜索仍需在对应平台执行后勾选。

当前完整实现分支为 `feature/open-web-job-agent`。登录 GitHub 后可直接打开 [创建合并请求](https://github.com/bashirmusamagaji19-debug/agentprogram/compare/master...feature/open-web-job-agent)，目标分支选择 `master`；合并前请确认最新 CI 为绿色。

## 发布前

- [x] GitHub 仓库包含 `streamlit_app.py`、`render.yaml` 和 `requirements.txt`
- [x] 仓库中没有真实 API key：使用 `git grep` 检查，只允许 `.env.example` 中的变量名
- [x] `python -m pytest` 全部通过
- [x] 本地 Streamlit 页面能用 Demo 模式完成一次搜索
- [x] `/healthz` 返回 `{"status":"ok"}`

## Streamlit Cloud

- [ ] Main file 选择 `streamlit_app.py`
- [ ] Secrets 配置 `TAVILY_API_KEY`
- [ ] 首次访问使用 Demo 模式确认页面可用
- [ ] 再使用 Online 模式确认真实搜索链路可用
- [ ] 复制公网 URL 到 README 或面试演示脚本

## Render

- [x] 仓库中的 `render.yaml` 已准备好 Web Service 配置
- [ ] 在 Render 中基于 `feature/open-web-job-agent` 创建 Web Service
- [ ] 配置 `TAVILY_API_KEY`，`DASHSCOPE_API_KEY` 仅在其他 Qwen CLI 链路需要时配置
- [ ] 打开 `<service-url>/healthz`
- [ ] 打开 `<service-url>/` 确认 FastAPI 原生页面可访问

## 演示边界

当前是单实例 Demo：运行状态在内存中，artifact 在实例本地目录。平台重启后历史运行记录可能丢失；这不影响实时搜索演示，但不能表述为多实例生产部署。
