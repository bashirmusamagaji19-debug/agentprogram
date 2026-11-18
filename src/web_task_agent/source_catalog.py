"""Dated, sampled employer catalog; legacy adapters remain loadable."""

VERIFIED_SOURCE_DATE = "2026-11-18"
VERIFIED_SOURCE_SCOPE = (
    "按各公司列出的关键词／全国抽查：发现岗位后检查真实详情、正文与可用申请入口。"
    "样本通过不保证所有关键词、城市或后续请求都有结果；每次交付仍重新核验。")
VERIFIED_SOURCES = {
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
    "liauto": {"name": "理想汽车", "official_url": "https://www.lixiang.com/employ.html",
               "checked_on": VERIFIED_SOURCE_DATE, "validation_keyword": "实习"},
    "nio": {"name": "蔚来", "official_url": "https://nio.jobs.feishu.cn/",
            "checked_on": VERIFIED_SOURCE_DATE, "validation_keyword": "AI 实习",
            "note": "本次目标 2 条、交付 1 条；其余详情未通过，保留为部分可用来源。"},
}

SOURCE_LIMITATIONS = {
    "腾讯": "当前校招接口返回空列表，暂不作为可选直连来源。",
    "百度": "当前公开接口拒绝访问，暂不作为可选直连来源。",
    "华为": "现有适配器仅覆盖社招，不能证明实习岗位覆盖；申请入口核验仍不稳定。",
    "京东、携程": "本次发现数据的招聘类型或详情核验未通过，暂不作为可选直连来源。",
    "比亚迪、拼多多": "本次未通过完整详情与条件核验，暂不作为可选直连来源。",
}
