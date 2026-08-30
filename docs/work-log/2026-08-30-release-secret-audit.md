# 发布前密钥与临时文件审计

- 使用仓库级文本扫描检查常见 `sk-`、`tvly-` key 外观，未发现真实密钥。
- `.env.example` 中原有带 `sk-` 前缀的占位符已改为 `your-...-key`，避免被 Secret Scanner 误报。
- `outputs/`、coverage 文件和临时 smoke PID 均被 `.gitignore` 忽略，未进入 Git 跟踪列表。
- 本次只做本地静态审计，没有读取或输出任何用户环境变量值。
