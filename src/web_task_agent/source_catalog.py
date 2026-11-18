"""Dated, sampled employer catalog; legacy adapters remain loadable."""

VERIFIED_SOURCE_DATE = "2026-11-18"
VERIFIED_SOURCE_SCOPE = (
    "按各公司列出的关键词／全国抽查：发现岗位后检查真实详情、正文与可用申请入口。"
    "样本通过不保证所有关键词、城市或后续请求都有结果；每次交付仍重新核验。")
VERIFIED_SOURCES = {
    "huawei": {"name": "华为", "official_url": "https://career.huawei.com/cn/campus-recruitment-job-list?recruitmentType=INTERN",
               "checked_on": VERIFIED_SOURCE_DATE, "validation_keyword": "AI Agent 实习"},
    "baidu": {"name": "百度", "official_url": "https://talent.baidu.com/jobs/list?recruitType=INTERN",
              "checked_on": VERIFIED_SOURCE_DATE, "validation_keyword": "AI Agent 实习"},
    "ctrip": {"name": "携程", "official_url": "https://careers.ctrip.com/",
              "checked_on": VERIFIED_SOURCE_DATE, "validation_keyword": "AI 实习",
              "note": "云端本轮 1/2：一条通过，另一条动态详情核验超时。"},
    "jd": {"name": "京东", "official_url": "https://campus.jd.com/#/jobs",
           "checked_on": VERIFIED_SOURCE_DATE, "validation_keyword": "AI 实习"},
    "tencent-campus": {"name": "腾讯", "official_url": "https://join.qq.com/post.html?query=p_2",
                       "checked_on": VERIFIED_SOURCE_DATE, "validation_keyword": "AI 实习"},
    "xiaomi": {"name": "小米", "official_url": "https://hr.xiaomi.com/", "checked_on": VERIFIED_SOURCE_DATE},
    "xiaohongshu": {"name": "小红书", "official_url": "https://job.xiaohongshu.com/",
                    "checked_on": VERIFIED_SOURCE_DATE},
    "xpeng": {"name": "小鹏", "official_url": "https://www.xiaopeng.com/", "checked_on": VERIFIED_SOURCE_DATE},
    "meituan": {"name": "美团", "official_url": "https://zhaopin.meituan.com/",
                "checked_on": VERIFIED_SOURCE_DATE, "validation_keyword": "AI 实习"},
    "netease": {"name": "网易", "official_url": "https://hr.163.com/",
                "checked_on": VERIFIED_SOURCE_DATE, "validation_keyword": "AI 实习"},
    "mihoyo": {"name": "米哈游", "official_url": "https://jobs.mihoyo.com/",
               "checked_on": VERIFIED_SOURCE_DATE, "validation_keyword": "AI 实习"},
    "nio": {"name": "蔚来", "official_url": "https://nio.jobs.feishu.cn/",
            "checked_on": VERIFIED_SOURCE_DATE, "validation_keyword": "AI 实习",
            "note": "本次目标 2 条、交付 1 条；其余详情未通过，保留为部分可用来源。"},
}

LIMITED_SOURCES = {
    "liauto": {"name": "理想汽车", "official_url": "https://www.lixiang.com/employ.html",
               "checked_on": VERIFIED_SOURCE_DATE, "validation_keyword": "实习"},
}

# Exact recruitment hosts. Shared providers must use the employer's own tenant.
WEB_SEARCH_HOSTS = {
    "xiaomi": ("xiaomi.jobs.f.mioffice.cn", "hr.xiaomi.com"),
    "xiaohongshu": ("job.xiaohongshu.com",),
    "xpeng": ("xiaopeng.jobs.feishu.cn",),
    "meituan": ("zhaopin.meituan.com",),
    "netease": ("hr.163.com",),
    "mihoyo": ("jobs.mihoyo.com",),
    "nio": ("nio.jobs.feishu.cn",),
    "huawei": ("career.huawei.com",),
    "baidu": ("talent.baidu.com",),
    "ctrip": ("careers.ctrip.com",),
    "jd": ("campus.jd.com",),
    "tencent-campus": ("join.qq.com",),
}

SOURCE_LIMITATIONS = {
    "理想汽车": "本机样本通过，但 Streamlit 和 Linux 云端发现接口连续超时，暂移出直连可选名单。",
    "比亚迪、拼多多": "官网实习频道可访问，本次未找到符合 AI 开发方向并通过详情和申请入口核验的样本。",
}
