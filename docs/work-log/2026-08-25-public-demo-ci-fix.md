# Public Demo CI 修复记录

## 现象

GitHub Actions 使用 Ubuntu + Python 3.11 运行公开 Demo 测试时，初次 run `32867830957` 失败：

- readiness 失败测试使用了 Windows 风格的非法路径，Linux 将其视为普通目录，因此没有返回 503；
- Streamlit `AppTest.from_file("streamlit_app.py")` 按测试文件目录解析相对路径，找不到仓库根目录的入口文件。

## 修复

- 使用临时目录中的普通文件作为“不可写 artifact 根目录”，跨平台稳定触发 readiness 失败；
- 使用 `Path(__file__).resolve().parents[2] / "streamlit_app.py"` 构造绝对入口路径。

## 证据

- 本地 Ruff 通过；
- 本地完整 coverage 测试：`373 passed`，总覆盖率 `90.69%`；
- 修复后的 GitHub Actions run `32868260069`：`success`。
