# Work Log — 2026-09-08b 阶段 3A-2:24 厂商官方 API 并行逆向与多源实时发现

> 分支:`feature/chinese-job-pipeline`
> 方法论:story #23(消费端 JS bundle 挖接口)

## 目标

解决 job-radar 快照时效问题的根源:直连各厂官方招聘 API 实时发现岗位。用户确认路线 A+B(官方列表 API 直连 + Bing SERP 兜底后置),逆向范围扩展至互联网大厂 + 车企 + 具身智能。

## 执行过程

1. **workflow 并行逆向**(48 agent:24 探测 + 24 独立复核):每家按"入口页 → JS bundle → 接口路径挖掘 → curl 实测"产出接口规格;复核 agent 独立重放验证(无鉴权/字段真实/岗位类型匹配)。
2. **复核裁决**:24 家全部探测成功,20 家复核通过;4 家 verify-fail 中快手被复核员摸清正确姿势后可救(收录),字节(社招 only + TLS 指纹)、阿里(校招批次未开)、长城(无 AI 岗 + WAF 封禁)记诚实边界。**实际收录 21 家**。
3. **适配器框架**:SaaS 家族(飞书招聘/Moka+AES/北森 zhiye)+ bespoke 声明式(宇树/小米/网易/小红书/米哈游)。
4. **真实冒烟逐家过**:11 个新源全部 200 + 有效岗位(传输层三修:curl_cffi TLS 指纹过飞书网关、gzip 显式声明、双通道回退 + 重试)。

## 关键技术点

- **飞书招聘系 TLS 指纹**:原生 urllib/curl 被 JA3 过滤(405),curl_cffi impersonate=chrome124 通过;显式 Accept-Encoding: gzip(br/zstd 解压在部分环境不可用)
- **Moka AES-CBC 信封**:密钥(necromancer)随响应自带、IV 在页面内嵌 TurboApply.data.aesIv(`de7c21ed8d6f50fe`)——公开协议,解密函数 we() 从站点 JS 直接可读,非破解
- **canonical 链路复用**(story #33):发现 URL → 官方 API 正文 → 链接落位可打开的详情页
- **域词扩展**:具身智能/机器人/自动驾驶并入命中口径(目标域新增车企与具身智能)

## 冒烟结果(2026-09-08 深夜)

11/11 新源 + 2 个既有源 = 13 发现源全通:腾讯(2027 校招实时)、美团、宇树、小米、网易、小红书、米哈游、小鹏、智元、银河通用、星动纪元、傅利叶、优必选。样例标题:大模型算法实习生(小米/网易)、具身智能算法实习生(小鹏)、Agentic RL 开发实习生(银河通用)、具身大模型算法工程师 VLA(星动纪元)、语音理解算法实习生(米哈游)。

## 诚实边界(不接/暂缓的厂商)

- **字节**:接口需浏览器级 TLS(curl_cffi 可过)但校招在 campus.bytedance.com(本机 TLS 全指纹握手断),社招岗全为"正式"——项目口径为实习/校招,不接
- **阿里**:接口可用(匿名 XSRF 双提交)但 campus 渠道 totalCount=0(校招批次未开),社招 245 条大模型岗不符合实习口径——批次开放后可接
- **长城汽车**:阿里云 WAF 连续请求即封禁(>35 分钟),且校招/实习池无 AI 岗——不接
- **百度**:探测/复核均通过,收尾冒烟遇 `no-auth illegal-visit` 限流——适配器待限流策略(节流/冷却)后补接
- **京东/拼多多/携程/华为/蔚来/理想/比亚迪/吉利**:复核通过,适配器未写(京东嵌套结构/拼多多/携程需详情二次或 HTML 清洗/蔚来 __NEXT_DATA__/理想需详情/吉利 AES 同 Moka 族)——下一批
- **快手**:复核员摸清正确姿势(`recruitSubProjectCodes:[]` 必填 + 全量 701 条 + 客户端过滤实习 116 个 AI 岗),URL 构造待补——下一批
- Bing SERP 发现(阶段 3B):本轮未启动,官方列表 API 直连已覆盖广度需求

## 第二批(同日,workflow 并行编写,agent 实测冒烟 8/8 通过)

- 京东/拼多多/携程/华为/蔚来/理想/比亚迪/吉利 8 家适配器上线;**口径调整:实习/校招/社招全收**(用户确认),过滤仅按域词(_title_in_domain)
- 亮点:京东分页参数实测无效(pageSize=1000 一次拉全量 350 条,全量域命中 134);蔚来详情落位飞书招聘接口(智元同构);吉利复用 Moka AES 解密器
- agent 产出代码的主会话合并教训:HTML 转义需 unescape;agent 内联 import(_strip_html/decrypt_moka_envelope)需收敛到模块顶部;Streamlit 文件被脚本毁坏时 git checkout 恢复重改比手修快
- **美团波动**:昨日冒烟 3 条,收尾冒烟 0 条(curl 交叉验证同样为空,message=成功)——服务端岗位数据刷新/批次结束,非适配器缺陷;适配器逻辑保留,等待数据恢复
- 最终:21 发现源中 20 家冒烟产出有效岗位;467 passed

## 阶段 3 最终收尾(同日)

- **百度补接**:适配器上线(form 表单 + INTERN/GRADUATE/SOCIAL 三类循环 + keyWord 服务端过滤)。限流为**冷却型**:no-auth illegal-visit 出现后冷却一段时间即恢复(实测三次:通→封→通),适配器遇 no-auth 诚实返回已获取部分。冒烟波动记录:单次冒烟可能遇限流拿 0 条,重试即恢复。
- **3B Bing SERP(实验性)上线**:SearchEngineSource 与其他发现源同构(_SPECS["bing-serp"]);discover_job_links 域名表精确化(门户级宽后缀 baidu.com/jd.com 等改为精确官方域,防百科/知道误收);cn.bing.com 端点。**实验结论(诚实)**:Bing 对脚本请求返回的结果不稳定(带 Cookie 的 curl 与无 Cookie 的 cffi 拿到不同内容),且 SERP 中岗位链接密度低——代码保留作实验能力,官方列表 API 直连(20 源)才是有效路径,泛搜查询保留"新站发现"用途。
- 传输层升级:form 表单支持(百度)、fetch_html(SERP)。
- 美团复查:服务端 jobList 仍空(curl 交叉验证),适配器保留。
- 最终:471 passed;23 发现源中 20 家稳定产出有效岗位,百度/美团受服务端波动,bing-serp 实验性。

## 测试与交付

- 450 passed(ruff 收敛至改动文件全绿);发现源 13 个全部注册 + Streamlit 多选默认全选
- 接口规格全量存档:`docs/results/official-api-specs-2026-09-08.json`(含复核意见)
- 依赖新增:pycryptodome(AES)、curl_cffi(TLS 指纹)→ requirements.txt + pyproject
