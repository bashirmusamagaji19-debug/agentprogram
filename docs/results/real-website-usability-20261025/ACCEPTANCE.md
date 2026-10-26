# 真实岗位网站可用性验收 · 2026-10-25

**状态：核心修复完成，真实服务入口与 Streamlit AppTest 闭环通过；完整浏览器 PDF 上传闭环仍待交互验收。** 不以单元测试、Demo 或回放代替真实来源。

## 实际运行与数据来源

本轮在计划指定的 agent-improvements 工作区实施，保留既有改动，未提交或改动原 checkout。默认来源为小米官方招聘，关键词 `AI Agent 实习`、地点 `全国`、目标 3，三个模型开关关闭。当天公开响应见 [source-check.md](source-check.md)、[public-jobs.json](public-jobs.json)、[source-responses.json](source-responses.json)。

列表返回的 URL 直接用于详情读取，未猜测小米岗位展示链接。列表 `type=3` 和详情 `recruit_type.id=202` 为已核实的实习证据；上层 `parent=校招`、应届或青云本身均不是实习依据。类型证据不补造主题或地点。

自动发现实际交付的三个岗位：

| 岗位 | 工作地 | 官方原文 | 详情类型 |
|---|---|---|---|
| agent应用实习生-2027届 | 北京 | [打开](https://xiaomi.jobs.f.mioffice.cn/internship/position/7662271237599725834/detail) | recruit_type.id=202 |
| AI 开发工程师（Agent 方向）-2027届 | 武汉 | [打开](https://xiaomi.jobs.f.mioffice.cn/internship/position/7698943066581584191/detail) | recruit_type.id=202 |
| Agent评估方向算法实习生 | 北京 | [打开](https://xiaomi.jobs.f.mioffice.cn/internship/position/7662657793955268910/detail) | recruit_type.id=202 |

以上三条每轮经实时详情读取，唯一 URL 无重复。达到 3 条即结束，没有声称检查了所有 20 个候选。默认最多检查 10 条，进度保留未检查数量。正向排障样本的另三条来源页面已经人工浏览器核对标题、城市、完整正文与实习标签。

## 两条真实正向入口

- 指定当天真实 URL：共享服务返回完整岗位、技能匹配和可打开的官方链接，约 1.5 秒。
- 自动搜索：每天实时从默认官方来源发现并交付目标 3 条，三次重复如下。

| 次数 | Run ID | 耗时（秒） | 岗位数 | 状态 | 保存 |
|---|---|---:|---:|---|---|
| 1 | run-5b46fb78 | 4.078 | 3 | succeeded | True |
| 2 | run-19ee597f | 2.235 | 3 | succeeded | True |
| 3 | run-0553954d | 2.375 | 3 | succeeded | True |

完整公开运行产物保存在 `portfolio-artifacts/usability-20261025/auto-repeat/`，摘要在 `auto-repeat-summary.json`。每轮独立数据库与来源采集，未用缓存或冻结回放冒充当前采集。同日三次成功只证明本日复现，不证明长期可用。

## Streamlit 页面与简历验收

程序化页面验收使用真实 Streamlit 入口（AppTest），通过文件上传组件载入文字型 PDF，再编辑预览、点击页面按钮、执行实际网络请求及持久化：

- single-url：1.907 秒，1 条，succeeded，saved=True。
- automatic：3.453 秒，3 条，succeeded，saved=True。

两份不同合成技能 PDF 对同一真实岗位得分分别为 1.00、0.00，技能标签均为空，低分岗位仍展示。预览修改、文件替换/移除清空和损坏/扫描/加密 PDF 阻止运行有 AppTest 回归。

用户随后授权用本人真实 PDF 验收。本地提取后去除姓名、电话、邮箱、学校名及非必要成绩，再将预览替换为脱敏文本后点击搜索。真实个人 PDF 的程序化页面链路成功交付并保存 3 条，约 4 秒，模型全部关闭。未修改原 PDF，未把简历发给岗位来源或模型服务；个人结果和脱敏文本只在忽略目录 `portfolio-artifacts/usability-20261025/private-local/`，不纳入本公开验收文件或测试夹具。

**浏览器交互边界：** 已在 Edge 打开本地页面确认默认小米、目标 3、空技能标签、上传及预览入口。浏览器扩展直接上传缺少本地文件权限；尝试系统选择框时 Computer Use 因无法可靠确认当前 Windows 浏览器 URL 而停止，本轮停止继续 UI 操作，未改扩展权限。故 AppTest 成功不等同于完整真实浏览器上传验收，计划中的此项保持未勾选。

## 失败与部分交付

以下使用 Streamlit 程序化页面和实时官方接口；超时项明确采用受控注入：

| 场景 | 结果 | 保存 | 解释 |
|---|---|---|---|
| one-for-three | partial / 1 条 | True | candidates_exhausted |
| wrong-city | failed / 0 条 | False | candidates_exhausted |
| all-invalid | failed / 0 条 | False | candidates_exhausted |
| controlled-source-deadline | failed / 0 条 | False | deadline_exceeded |

- 单条候选但目标 3：返回 partial，保留匹配与保存，原因是本轮候选已用完。
- 武汉岗位请求北京：返回 0 条并记录地点冲突，不为填满结果放宽地点。
- 明确不存在的岗位 ID=0：详情链出现 HTTP 404 后停止，返回 0 条，不以列表正文覆盖移除信号。
- 来源超时：通过受控 TimeoutError 验证页面给出总时限说明，未发起业务保存；未伪称观察了供应商实际 90 秒超时。
- 后续列表页失败：回归确认前页已发现岗位被保留、来源标为 partial，不重复浪费剩余时限重跑同一失败来源。
- 默认预算按候选数计算；只剩 3 步时先匹配、保存、结束。显式极小预算只剩 1 步时保存已验证岗位并标记 partial，未生成的匹配明确提示，不超预算写入。保存前人工审批仍有效。

## 发布检查与改动归属

完整发布检查日志：`portfolio-artifacts/usability-20261025/release-check.log`。初跑为 1023 passed、2 failed、1 skipped，覆盖率 87.98%；ruff、wheel、doctor、strict HITL、diff-check 均通过。两个失败已定位并修复：网页候选上限误影响 CLI 显式 aggregator-limit；梯队测试旧夹具把应届岗位当作实习。保留 CLI 显式限制，网页入口单独传上限 10；梯队用例改为真正实习标题，未修改 v4 冻结数据。后续按计划只针对修复重验，最终 365 项受影响回归通过（`final-regression-after-matcher.log`），最新 ruff、wheel 与 diff-check 均通过。最终证据见 `final-checks.json` 与对应日志；不把初跑失败写成完整全量绿色。

按任务保存的本轮增量补丁位于 `portfolio-artifacts/usability-20261025/patches/`；基线源文件和原先 git diff 保存在该目录的父级。个人简历内容未进入补丁。

## 启动与已知限制

当前本地服务：[http://127.0.0.1:8510](http://127.0.0.1:8510)。关闭后可在本工作区运行：

```powershell
.\scripts\start_website.ps1
```

启动脚本固定使用该工作区的 `src`，避免已有 editable 安装指向另一 checkout。默认绑定本机地址，依赖现有 Agent/.venv。上传 PDF 后先核对/编辑文字，再保持默认查询或输入自己的城市和关键词，点击开始。

- 默认小米来源是本轮验收对象；其余来源仍属于高级选择，不能声称全部可用。
- 扫描 PDF/需要密码的 PDF 暂不识别，网页会阻止运行并提示更换或粘贴文字。
- 规则评分只表示词表内已识别技能覆盖率，不是全部任职条件或录用概率。
- 极深的自定义 Windows 产物路径可能超过系统路径限制。本次默认工作区路径可用，较深的验收目录出现过 metadata 临时路径错误，保留失败日志；真实简历验收改用短 C:\Temp 运行根目录，未重写存储系统。
- 总时限 90 秒与每 URL 最多两次打开控制的是本次请求；不能保证第三方库存和网络长期稳定。
- 本轮未自动投递、部署云端或新增架构；最终浏览器交互验收保持开放。


实际简历核对追加：原规则将 LangGraph 与 LangChain 合并，现已拆分为独立技能，避免把相关框架当作已掌握证据。用脱敏文本再次从实时接口搜索后已保存到默认网站本地历史；个人匹配分、届别核对与原文链接只在 private-local/search-results.md。
