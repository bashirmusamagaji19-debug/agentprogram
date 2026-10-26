# 真实岗位来源核验（2026-10-25）

本文件对应计划《2026-10-25 真实岗位网站可用性修复》的任务 1。核对范围为当天已保存的公开岗位数据与公开接口响应；采集时区为 Asia/Shanghai。本文没有重新运行测试、没有重新占用浏览器，也没有读取或写入用户简历。

## 来源选择

**本轮选择小米实习生官网作为正向来源。** 使用的列表请求为：

```text
https://hr.xiaomi.com/website/api/agent/searchJobPage?keyword=Agent&pageSize=50&pageNum=1&type=3
```

保存的响应显示该请求返回 45 条，`total=45`、`pageTotal=1`；45 条列表记录的 `type` 均为 `3`。三份入选岗位的详情接口均返回 `code=0`、`channel_online_status=1`，且详情中有非空的 `description` 与 `requirement`。详见 [source-responses.json](source-responses.json#/xiaomi_intern) 与 [xiaomi.json](../../../portfolio-artifacts/usability-20261025/source-probes/xiaomi.json)。

**腾讯本轮不作为正向来源。** 已采集的腾讯首屏响应为 50 条，接口声明的总数为 904 条；首屏 50 条中，`projectId` 只有 `1`，`projectName` 与 `recruitLabelName` 的去重值都只有“应届毕业生”，标题中含“实习”的记录为 0 条。首条是“AI全栈工程师”，字段原值如下：

```json
{
  "id": 22444,
  "positionTitle": "AI全栈工程师",
  "projectId": 1,
  "projectName": "应届毕业生",
  "recruitLabelName": "应届毕业生",
  "workCities": "深圳总部 北京 上海 广州 成都 杭州 "
}
```

以上只能证明**腾讯首批 50 条**是应届招聘，不能外推为腾讯全部 904 条都没有实习库存。详见 [source-responses.json](source-responses.json#/tencent_first_page) 和 [tencent.json](../../../portfolio-artifacts/usability-20261025/source-probes/tencent.json)。因此本轮采用“小米已有明确实习详情字段”的样本，腾讯首屏仅作为当天失败/负证据保留。

## 官方实习字段与判定边界

小米列表的 `type=3` 是已核实的官方实习类型证据；它只补足招聘类型，不能代替详情正文、城市、主题和链接活性的核验。三份入选岗位的详情都包含同一条结构化字段，路径为：

```text
detail_response.data.job_post_detail.recruit_type
```

其值为：

```json
{
  "id": "202",
  "name": "实习",
  "en_name": "Intern",
  "depth": 2,
  "parent": {
    "id": "2",
    "name": "校招",
    "en_name": "Campus",
    "depth": 1
  }
}
```

本轮接受的正向依据是 `recruit_type.id=202`、`recruit_type.name=实习`、`recruit_type.depth=2`。`recruit_type.parent.id=2`、`parent.name=校招` 描述的是上层校招招聘体系，**parent 为“校招”不等于本条岗位本身是校招，也不能把 parent=校招 单独当作实习证明**。招聘类型由子节点 `id=202/name=实习` 决定；页面同时展示“校招/实习”时，应按这两个层级分别记录。

判定边界如下：

- 详情 `recruit_type` 明确为 `id=202/name=实习`：可作为实习类型证据。
- 列表 `type=2`，或详情/岗位要求明确为应届、校招：属于校招负样本，不进入实习交付。
- 只有列表 `type=3`，但尚未读取详情：类型已有依据，岗位正文和当前链接仍待验证。年份“2027届”或 parent=校招不能补足类型。
- 官方类型缺失且标题/正文也无明确实习依据：类型未知，不计入实习交付；不凭聚合标签或应届年份推断。明确的官方非实习类型优先于正文中“实习经验优先”等字样。
- `parent.name=校招` 本身既不能否定 `id=202/name=实习`，也不能把 `type=2` 的校招岗位改判为实习。

小米实习列表的 45 条记录均为列表结构，列表项本身没有 `recruit_type` 字段；本轮三份样本已经通过各自详情补齐该字段。其余 42 条仍只有 `type=3` 和列表字段，未取得详情 `recruit_type`，因此在完成详情核验前仍不计入当天已确认可交付库存。

## 3 个正向排障样本

共同采集信息：小米列表探针的 `captured_at` 为 `2026-10-25T15:15:10.710377+08:00`；逐岗位的详情采集时刻取 `public-jobs.json` 顶层 `captured_at`。城市 ID 使用详情字段 `city_info.code`，城市名使用 `city_info.name`，并与列表 `cityZhNames` 对照。

| # | 精确标题 | 城市（详情城市 ID） | 精确采集时刻（+08:00） | 真实 ID | 可打开链接 |
|---|---|---|---|---|---|
| 1 | AI 开发工程师（Agent 方向）-2027届 | 武汉（CT_154） | 2026-10-25T15:16:50.619032 | 列表 id=1754602；jobId=7698943066581420351；jobPostId=7698943066581584191 | https://xiaomi.jobs.f.mioffice.cn/internship/position/7698943066581584191/detail |
| 2 | Miclaw-AI agent开发实习生 | 北京（CT_11） | 2026-10-25T15:16:52.417430 | 列表 id=3449；jobId=7621513292817516806；jobPostId=7621513292817729798 | https://xiaomi.jobs.f.mioffice.cn/internship/position/7621513292817729798/detail |
| 3 | AI Agent算法实习生-2027届 | 北京（CT_11） | 2026-10-25T15:16:53.199267 | 列表 id=545407；jobId=7687596167349897515；jobPostId=7687596167350061355 | https://xiaomi.jobs.f.mioffice.cn/internship/position/7687596167350061355/detail |

### 样本 1：AI 开发工程师（Agent 方向）-2027届

- 列表标题 `list_item.title`、详情标题 `detail_response.data.job_post_detail.title` 和已保存页面 `<title>` 均为“AI 开发工程师（Agent 方向）-2027届”。公开页面探针见 [xiaomi-page.html](../../../portfolio-artifacts/usability-20261025/source-probes/xiaomi-page.html)。
- 城市：列表 `list_item.cityZhNames[0]="武汉"`；详情 `city_info.code="CT_154"`、`city_info.name="武汉"`。
- 类型：列表 `list_item.type=3`；详情 `recruit_type.id="202"`、`name="实习"`、`depth=2`、`parent.id="2"`、`parent.name="校招"`。
- 正文：详情 `description` 为 499 字符，`requirement` 为 556 字符；`channel_online_status=1`，列表 `list_item.publishTime=2026-10-21`。
- 浏览器核验状态：主代理已手动打开页面，页面显示武汉/校招/实习，与上述 API 城市和招聘类型层级一致。
- 原始 JSON：[public-jobs.json](public-jobs.json#/0)；列表原始副本 [xiaomi.json](../../../portfolio-artifacts/usability-20261025/source-probes/xiaomi.json#/response/data/list/1)；列表响应副本 [source-responses.json](source-responses.json#/xiaomi_intern/response/data/list/1)；详情原始副本 [xiaomi-detail.json](../../../portfolio-artifacts/usability-20261025/source-probes/xiaomi-detail.json#/response/data/job_post_detail)。

### 样本 2：Miclaw-AI agent开发实习生

- 列表标题和详情标题均为“Miclaw-AI agent开发实习生”。
- 城市：列表 `list_item.cityZhNames[0]="北京"`；详情 `city_info.code="CT_11"`、`city_info.name="北京"`。
- 类型：列表 `list_item.type=3`；详情 `recruit_type.id="202"`、`name="实习"`、`depth=2`、`parent.id="2"`、`parent.name="校招"`。
- 正文：详情 `description` 为 601 字符，`requirement` 为 404 字符；`channel_online_status=1`，列表 `list_item.publishTime=2026-03-26`。
- 浏览器核验状态：主代理已手动打开页面，页面显示北京/校招/实习，与上述 API 城市和招聘类型层级一致。
- 原始 JSON：[public-jobs.json](public-jobs.json#/1)；列表原始副本 [xiaomi.json](../../../portfolio-artifacts/usability-20261025/source-probes/xiaomi.json#/response/data/list/3)；列表响应副本 [source-responses.json](source-responses.json#/xiaomi_intern/response/data/list/3)。该样本的详情响应保存在 `public-jobs.json` 的 `detail_response` 中，没有单独的逐岗位详情探针文件。

### 样本 3：AI Agent算法实习生-2027届

- 列表标题和详情标题均为“AI Agent算法实习生-2027届”。
- 城市：列表 `list_item.cityZhNames[0]="北京"`；详情 `city_info.code="CT_11"`、`city_info.name="北京"`。
- 类型：列表 `list_item.type=3`；详情 `recruit_type.id="202"`、`name="实习"`、`depth=2`、`parent.id="2"`、`parent.name="校招"`。
- 正文：详情接口有 `description` 154 字符和 `requirement` 108 字符，`channel_online_status=1`，列表 `list_item.publishTime=2026-09-20`。这是 API 返回的正文，**不等于浏览器页面正文已经完成核验**。
- 浏览器核验状态：浏览器已显示完整职责、任职要求、北京及“校招 / 实习”，与 API 一致。
- 原始 JSON：[public-jobs.json](public-jobs.json#/2)；列表原始副本 [xiaomi.json](../../../portfolio-artifacts/usability-20261025/source-probes/xiaomi.json#/response/data/list/4)；列表响应副本 [source-responses.json](source-responses.json#/xiaomi_intern/response/data/list/4)。该样本的详情响应同样保存在 `public-jobs.json` 的 `detail_response` 中，没有单独的逐岗位详情探针文件。

## 失败与负样本

### 类型 2 的校招负样本

[source-responses.json](source-responses.json#/xiaomi_campus/data/list/0) 中的“Agent工程师”是明确的反例：

- 标题：`Agent工程师`
- 城市：`武汉,北京`（列表 `cityZhNames`，本次未做详情城市 ID 核验）
- `type=2`，URL 为 `https://xiaomi.jobs.f.mioffice.cn/campus/position/7670928909127223588/detail`
- `jobPostId=7670928909127223588`
- `requirement` 开头明确写“2027届应届毕业生”
- 列表项没有 `recruit_type`

该记录应作为校招负样本排除。仅看到它是“2027届”或看到 `parent=校招` 时，也不能把它转成实习。当前保存的是校招列表数据，没有该岗位的详情响应，因此不能声称已核对该详情的 `recruit_type`；这里可确认的负证据是 `type=2`、`/campus/` 链接和应届要求。`xiaomi_campus.data` 显示 `total=69`、`pageTotal=14`，本轮公开响应只保存了首屏 5 条，5 条全部是 `type=2` 且都没有 `recruit_type`。

### 字段缺失时的未知状态

`xiaomi_campus` 的 5 条列表项均没有 `recruit_type`；这只能说明列表结构没有提供该字段，不能据此推导详情的类型。类似地，`xiaomi_intern` 的 45 条列表项虽然都是 `type=3`，但列表层也没有 `recruit_type`。在没有详情证据时，正确状态是“未知/待验证”，并继续读取详情；不能把标题、年份、列表分类或 parent 字段拼成实习结论。

## 证据缺口

1. **完整浏览器上传闭环未验。** 三份来源页均完成正文核对，但本地网站的浏览器 PDF 上传受扩展权限及 Computer Use 停止影响，不能以来源页核对代替整站验收。程序化 Streamlit 页面验收另见 ACCEPTANCE.md。
2. **腾讯只核对了首屏。** 50/904 条全部是“应届毕业生”，但未翻页或做全量检索，不能据此断言腾讯全站没有实习岗位；腾讯只能作为本轮首屏失败样本。
3. **小米剩余候选未取详情。** 45 条实习列表中有 42 条没有详情 `recruit_type`；它们仍属于待验证候选，不能直接计入可交付库存。
4. **校招负样本没有详情响应。** `type=2` 的“Agent工程师”可依据列表类型、`/campus/` 链接和应届要求排除，但没有单独的详情 `recruit_type` 原始值可对照。
5. **独立详情探针缺少独立采集时间。** [xiaomi-detail.json](../../../portfolio-artifacts/usability-20261025/source-probes/xiaomi-detail.json) 只有 `url` 和 `response`，没有自己的 `captured_at`；本文的逐岗位采集时刻取 `public-jobs.json` 的 `captured_at`。

本轮可确认的来源选择是：小米实习官网有 3 条可用的正向排障候选，三条均完成浏览器页面正文、标题、城市和类型的一致性核验；腾讯首批因只有应届证据而不选。



后续浏览器复核：第三条 AI Agent算法实习生-2027届 已实际显示完整职责、要求、北京和“校招 / 实习”，与 API 一致。三条正向排障样本均完成网页核对。自动发现的另三条交付记录及重复运行见 ACCEPTANCE.md。
