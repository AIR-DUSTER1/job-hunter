# -*- coding: utf-8 -*-
"""岗位打分与外包识别。

打分维度（满分 100）：
- 岗位匹配度 35：职位名是否命中目标方向
- 薪资匹配   25：薪资区间与期望的重合度
- 经验匹配   15：岗位经验要求与自身年限的贴合度
- 公司质量   15：规模 / 融资 / 行业
- 活跃度     10：HR 最近活跃 + 上架时间新鲜度

外包识别分两层：
1. 硬信号：姓名关键词、公司名关键词、JD 关键词（命中即判"是"）
2. 软信号：人力服务行业、无实体业务比例过高等（判"待确认"）
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------- 用户画像

@dataclass
class Profile:
    keywords: list[str]          # 目标岗位关键词
    city: str                    # 目标城市
    salary_min: float            # 期望月薪下限（K）
    salary_max: float            # 期望月薪上限（K）
    years: float                 # 工作年限
    exclude_outsourcing: bool = True


# ---------------------------------------------------------------- 外包识别

# 出现在公司名里，几乎必然是人力外包/驻场
OUTSOURCE_COMPANY_TOKENS = [
    "人力资源", "人力服务", "劳务", "派遣", "外派", "外包", "驻场",
    "人才服务", "人才管理", "人才发展", "人才科技", "猎头",
    "信息技术服务", "软件服务", "系统集成服务",
    "科锐", "中软国际", "软通动力", "文思海辉", "博彦", "中电金信",
    "佰钧成", "法本", "中科软", "亚信", "中软", "东软集团",
    "纬创", "易宝", "京北方", "神州数码", "思特沃克", "微创软件",
    "佰钧成", "艾克斯特", "柯莱特", "深信服外包",
]

# JD 里出现这些，说明是驻场/派遣
OUTSOURCE_JD_TOKENS = [
    "驻场", "外派", "外包", "派遣", "人力外包", "项目外包",
    "客户现场", "甲方现场", "驻客户", "驻场开发", "外包岗位",
    "长期项目", "按人天", "人天", "结算周期", "入职甲方",
]

# 行业字段命中即高度怀疑
OUTSOURCE_INDUSTRY_TOKENS = ["人力资源", "人力服务", "劳务派遣", "中介服务"]


def judge_outsourcing(company: str, industry: str, jd: str, title: str = "") -> tuple[str, str]:
    """返回 (判定, 理由)。判定取 是 / 否 / 待确认。"""
    company = company or ""
    industry = industry or ""
    jd = jd or ""
    title = title or ""

    hits: list[str] = []
    for tok in OUTSOURCE_COMPANY_TOKENS:
        if tok in company:
            hits.append(f"公司名含「{tok}」")
    if hits:
        return "是", "；".join(hits)

    jd_hits = [tok for tok in OUTSOURCE_JD_TOKENS if tok in jd]
    if jd_hits:
        return "是", "JD 含「" + "」「".join(jd_hits[:3]) + "」"

    if any(tok in industry for tok in OUTSOURCE_INDUSTRY_TOKENS):
        return "待确认", f"所属行业为「{industry}」，疑似人力服务"

    if "外包" in title:
        return "是", "职位名含「外包」"

    return "否", ""


# ---------------------------------------------------------------- 薪资解析

_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万|k|K|千|元)?\s*[-~－至]\s*(\d+(?:\.\d+)?)\s*(万|k|K|千|元)?")
_SINGLE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万|k|K|千|元)")


def _to_k(value: float, unit: str | None, fallback_unit: str = "") -> float:
    """把带单位的值折算成 K（千元）。unit 缺失时沿用 fallback_unit。"""
    u = unit or fallback_unit
    if u == "万":
        return value * 10
    if u in ("千", "k", "K"):
        return value
    if u == "元":
        return value / 1000
    # 无任何单位线索：小于 100 视为 K，否则视为元
    return value if value < 100 else value / 1000


def parse_salary(text: str) -> float:
    """解析薪资文本为月薪中位数（单位 K）。解析失败返回 0。

    支持：`10-18k`、`1-1.2万`、`7千-1.2万`、`8-12k·15薪`、`20-25k`。
    """
    if not text:
        return 0.0
    s = text.replace(" ", "")
    m = _RANGE_RE.search(s)
    if m:
        lo_v, lo_u, hi_v, hi_u = m.group(1), m.group(2), m.group(3), m.group(4)
        # 混合单位时，以「万」那一侧为准补齐另一侧
        unit = "万" if "万" in (lo_u or "", hi_u or "") else (lo_u or hi_u or "")
        lo = _to_k(float(lo_v), lo_u, unit)
        hi = _to_k(float(hi_v), hi_u, unit)
        return (lo + hi) / 2
    m = _SINGLE_RE.search(s)
    if m:
        return _to_k(float(m.group(1)), m.group(2))
    return 0.0


# ---------------------------------------------------------------- 工作时间

_REST_PATTERNS = [
    ("双休", "双休"),
    ("周末双休", "双休"),
    ("大小周", "大小周"),
    ("单休", "单休"),
    ("单双休", "单双休"),
    ("做五休二", "双休"),
    ("做六休一", "单休"),
    ("五天工作制", "双休"),
    ("弹性工作", "弹性"),
]

_HOURS_RE = re.compile(
    r"(\d{1,2}[:：]\d{2})\s*[-~－至到]\s*(\d{1,2}[:：]\d{2})"
)
_HOURS_CN_RE = re.compile(r"(\d{1,2})\s*[点:：]\s*(半)?\s*[-~－至到]\s*(\d{1,2})\s*[点:：]\s*(半)?")
# 「早9点-晚6点」「上午9点到下午6点」这类带时段词的说法
_HOURS_CN2_RE = re.compile(
    r"(早|上午|下午|晚|晚上)?\s*(\d{1,2})\s*[点:：]"
    r"(?:\s*[-~－至到]\s*)"
    r"(早|上午|下午|晚|晚上)?\s*(\d{1,2})\s*[点:：]"
)

# 只有出现在这些语境附近的时段才算上班时间，否则很可能是页脚的
# 「客服热线工作日 8:00-22:00」这类噪声。
_HOURS_CONTEXT = (
    "上班", "下班", "工作时间", "作息", "弹性", "朝九", "打卡",
    "排班", "双休", "单休", "小时工作制", "工时", "午休", "周末",
)


def parse_work_rest(text: str) -> str:
    """识别单双休。按优先级匹配，避免「大小周」被「双休」误命中。"""
    t = text or ""
    for pat, label in _REST_PATTERNS:
        if pat in t:
            return label
    return ""


def parse_work_hours(text: str) -> str:
    """从文本中提取上下班时间。

    只在出现「上班 / 下班 / 工作时间」等语境词附近才认定，
    避免把页脚的客服热线服务时间误当成上班时间。
    """
    text = text or ""
    for m in _HOURS_RE.finditer(text):
        lo, hi = text[max(0, m.start() - 40): m.start()], text[m.end(): m.end() + 20]
        near = lo + hi
        if not any(k in near for k in _HOURS_CONTEXT):
            continue
        a, b = m.group(1).replace("：", ":"), m.group(2).replace("：", ":")
        # 过滤明显不合理的排班（客服热线那种超长跨度且无语境）
        try:
            ha = int(a.split(":")[0])
            hb = int(b.split(":")[0])
        except ValueError:
            continue
        if hb - ha <= 0 or hb - ha > 14:
            continue
        return f"{a}-{b}"

    for m in _HOURS_CN_RE.finditer(text):
        near = text[max(0, m.start() - 40): m.end() + 20]
        if not any(k in near for k in _HOURS_CONTEXT):
            continue
        a = m.group(1) + (":30" if m.group(2) else ":00")
        b = m.group(3) + (":30" if m.group(4) else ":00")
        try:
            if not 0 < int(b[:2]) - int(a[:2]) <= 14:
                continue
        except ValueError:
            continue
        return f"{a}-{b}"

    for m in _HOURS_CN2_RE.finditer(text):
        # 「早9点-晚6点」本身就带时段词，可直接认定为作息；
        # 否则仍要求上下文里有上班/下班等词
        explicit_period = m.group(1) in ("早", "上午", "下午", "晚", "晚上") or \
                          m.group(3) in ("早", "上午", "下午", "晚", "晚上")
        near = text[max(0, m.start() - 30): m.end() + 20]
        if not explicit_period and not any(k in near for k in _HOURS_CONTEXT):
            continue
        pre_a, ha, pre_b, hb = m.group(1), m.group(2), m.group(3), m.group(4)
        a_int, b_int = int(ha), int(hb)
        # 「早9点-晚6点」：下午/晚 的时间按 12 小时制补 12
        if pre_b in ("下午", "晚", "晚上") and b_int < 12:
            b_int += 12
        if pre_a in ("下午", "晚", "晚上") and a_int < 12:
            a_int += 12
        if not 0 < b_int - a_int <= 14:
            continue
        return f"{a_int:02d}:00-{b_int:02d}:00"
    return ""


# ---------------------------------------------------------------- 打分

_SENIORITY_PENALTY = {
    # 岗位经验要求 -> (最低年, 最高年)；None 表示不限
    "应届": (0, 0.5), "在校": (0, 1), "经验不限": (0, 99),
    "1年以内": (0, 1), "1-3年": (1, 3), "3-5年": (3, 5),
    "5-10年": (5, 10), "10年以上": (10, 99),
}


def _parse_year_range(text: str) -> tuple[float, float] | None:
    t = (text or "").replace(" ", "")
    if not t:
        return None
    for key, rng in _SENIORITY_PENALTY.items():
        if key in t:
            return rng
    m = re.search(r"(\d+)\s*年以上", t)
    if m:
        v = float(m.group(1))
        return (v, 99)
    return None


def score_title(title: str, keywords: list[str]) -> float:
    """岗位名与目标关键词的匹配度，0-35。"""
    t = (title or "").lower()
    if not t:
        return 0.0
    best = 0.0
    for kw in keywords:
        k = kw.lower()
        if k in t:
            best = max(best, 35.0 if t.index(k) < 12 else 30.0)
        else:
            # 关键词分词命中
            parts = [p for p in re.split(r"[\s/、,，]+", k) if len(p) > 1]
            hit = sum(1 for p in parts if p in t)
            if parts and hit:
                best = max(best, 35.0 * hit / len(parts) * 0.8)
    return best


def score_salary(salary_k: float, lo: float, hi: float) -> float:
    """薪资重合度，0-25。"""
    if salary_k <= 0:
        return 12.0  # 未知给居中分，不惩罚也不奖励
    if salary_k < lo:
        gap = (lo - salary_k) / max(lo, 1)
        return max(0.0, 25.0 * (1 - gap * 2))
    if salary_k > hi:
        gap = (salary_k - hi) / max(hi, 1)
        return max(0.0, 25.0 * (1 - gap * 0.5))
    # 区间内：越靠中上越好
    mid = (lo + hi) / 2
    pos = 1 - abs(salary_k - mid) / max(mid - lo, 1)
    return 18.0 + 7.0 * max(0.0, min(1.0, pos))


def score_experience(exp_text: str, years: float) -> float:
    """经验匹配，0-15。"""
    rng = _parse_year_range(exp_text)
    if rng is None:
        return 9.0
    lo, hi = rng
    if lo <= years <= hi:
        return 15.0
    if years < lo:
        return max(0.0, 15.0 - (lo - years) * 6)
    return max(0.0, 15.0 - (years - hi) * 4)


def score_company(size: str, financing: str, industry: str) -> float:
    """公司质量，0-15。"""
    pts = 0.0
    size = size or ""
    # 规模：100-999 最稳；过小抗风险差，超大可能流程重
    if re.search(r"100-499|100-999|150-500", size):
        pts += 7
    elif re.search(r"500-999|500-2000|1000", size) or "5000" in size:
        pts += 6
    elif re.search(r"20-99|少于50|1-49", size):
        pts += 3.5
    elif "10000" in size or "人以上" in size:
        pts += 5
    else:
        pts += 4

    fin = financing or ""
    if any(k in fin for k in ("已上市", "上市", "不需要融资")):
        pts += 5
    elif any(k in fin for k in ("C轮", "D轮", "战略融资", "E轮")):
        pts += 4
    elif any(k in fin for k in ("B轮", "A轮", "天使轮")):
        pts += 2.5
    else:
        pts += 3

    ind = industry or ""
    if any(k in ind for k in ("人工智能", "互联网", "计算机软件", "IT服务", "软件")):
        pts += 3
    elif any(k in ind for k in ("人力资源", "中介")):
        pts += 0
    else:
        pts += 1.5
    return min(15.0, pts)


def score_activity(hr_active: str, published: str) -> float:
    """活跃度，0-10。"""
    pts = 0.0
    h = hr_active or ""
    if any(k in h for k in ("刚刚", "在线", "今日", "1小时", "小时前")):
        pts += 6
    elif any(k in h for k in ("12小时", "24小时", "昨日", "昨天", "1天")):
        pts += 4.5
    elif any(k in h for k in ("2天", "3天", "本周", "天内")):
        pts += 3
    elif any(k in h for k in ("周", "月")):
        pts += 1.5
    else:
        pts += 2

    p = published or ""
    if any(k in p for k in ("刚刚", "今天", "今日", "小时前")):
        pts += 4
    elif any(k in p for k in ("昨天", "1天", "天内")):
        pts += 3
    elif any(k in p for k in ("近一周", "本周", "7天")):
        pts += 2
    elif any(k in p for k in ("月", "30天")):
        pts += 1
    else:
        pts += 1.5
    return min(10.0, pts)


def score_job(job, profile: Profile) -> tuple[float, str]:
    """综合打分，返回 (总分, 拆解说明)。"""
    t = score_title(job.title, profile.keywords)
    s_k = parse_salary(job.salary)
    s = score_salary(s_k, profile.salary_min, profile.salary_max)
    e = score_experience(job.experience, profile.years)
    c = score_company(job.company_size, job.company_financing, job.company_industry)
    a = score_activity(job.hr_active, job.published)

    total = t + s + e + c + a

    # 外包扣分
    if job.is_outsourcing == "是":
        total *= 0.35
    elif job.is_outsourcing == "待确认":
        total *= 0.8

    detail = (
        f"岗位{t:.0f}/35 薪资{s:.0f}/25 经验{e:.0f}/15 "
        f"公司{c:.0f}/15 活跃{a:.0f}/10"
    )
    if job.is_outsourcing == "是":
        detail += " ×外包折减"
    elif job.is_outsourcing == "待确认":
        detail += " ×外包待确认折减"
    return round(min(100.0, total), 1), detail
