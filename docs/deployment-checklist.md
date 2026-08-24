# 公开演示部署检查清单

## 发布前

- [ ] GitHub 仓库默认分支包含 `streamlit_app.py`、`render.yaml` 和 `requirements.txt`
- [ ] 仓库中没有真实 API key：使用 `git grep` 检查，只允许 `.env.example` 中的变量名
- [ ] `python -m pytest` 全部通过
- [ ] 本地 Streamlit 页面能用 Demo 模式完成一次搜索
- [ ] `/healthz` 返回 `{"status":"ok"}`

## Streamlit Cloud

- [ ] Main file 选择 `streamlit_app.py`
- [ ] Secrets 配置 `TAVILY_API_KEY`
- [ ] 首次访问使用 Demo 模式确认页面可用
- [ ] 再使用 Online 模式确认真实搜索链路可用
- [ ] 复制公网 URL 到 README 或面试演示脚本

## Render

- [ ] 使用仓库中的 `render.yaml` 创建 Web Service
- [ ] 配置 `TAVILY_API_KEY`，`DASHSCOPE_API_KEY` 仅在其他 Qwen CLI 链路需要时配置
- [ ] 打开 `<service-url>/healthz`
- [ ] 打开 `<service-url>/` 确认 FastAPI 原生页面可访问

## 演示边界

当前是单实例 Demo：运行状态在内存中，artifact 在实例本地目录。平台重启后历史运行记录可能丢失；这不影响实时搜索演示，但不能表述为多实例生产部署。
