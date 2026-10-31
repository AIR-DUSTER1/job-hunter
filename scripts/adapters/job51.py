# -*- coding: utf-8 -*-
"""前程无忧（51job）适配器。

列表页 we.51job.com 会把结构化数据写进卡片的 sensorsdata 属性，
含 jobId / 薪资 / 地区 / 经验 / 学历 / 发布时间，无需进详情页即可拿到大部分字段。
详情页 jobs.51job.com/all/{jobId}.html 补 JD、地址、公司简介。
"""

from __future__ import annotations

import json
import re
import urllib.parse

from ..core.browser import body_text, get_page, safe_ele, safe_eles, safe_get
from ..core.models import Job
from ..core.scoring import parse_work_hours, parse_work_rest

CITY_CODE = {"青岛": "120300", "济南": "120200", "北京": "010000", "上海": "020000",
             "深圳": "040000", "杭州": "080200", "成都": "090200"}
BASE = "https://we.51job.com/pc/search"
DETAIL = "https://jobs.51job.com/all/{}.html"
MAX_PAGE = 3


def _parse_cards(page) -> list[Job]:
    """从列表页卡片解析出 Job 列表。"""
    cards = safe_eles(page, "css:.joblist-item")
    jobs: list[Job] = []
    for card in cards:
        try:
            payload = _read_sensors(card)
            text = (card.text or "").replace("\n", "|")
            job = Job(platform="前程无忧")
            job.salary = payload.get("jobSalary", "")
            job.title = payload.get("jobTitle", "")
            job.city = payload.get("jobArea", "")
            job.experience = payload.get("jobYear", "")
            job.education = payload.get("jobDegree", "")
            job.published = payload.get("jobTime", "")
            job_id = payload.get("jobId", "")
            if job_id:
                job.apply_url = DETAIL.format(job_id)

            # 公司名与公司信息在右侧 a.comp
            comp = card.ele("css:a.comp", timeout=1)
            if comp:
                cname = comp.ele("css:.cname", timeout=1)
                if cname:
                    job.company = (cname.text or "").strip()
                dcs = comp.eles("css:.dc")
                vals = [(d.text or "").strip() for d in dcs if d.text]
                if vals:
                    job.company_industry = vals[0]
                for v in vals:
                    if re.search(r"人|以上", v):
                        job.company_size = v
                    elif v in ("民营", "外资（欧美）", "国企", "合资", "上市公司", "创业公司", "外资（非欧美）", "事业单位"):
                        job.company_financing = v

            # HR 活跃提示
            tip = card.ele("css:.joblist-item-jobinfo .tip", timeout=1)
            if tip:
                job.hr_active = (tip.text or "").strip()

            # 福利/技能标签：全在 .tag 里，后面若干项是福利
            tags = [(t.text or "").strip() for t in card.eles("css:.tag") if t.text]
            job.welfare = _split_welfare(tags)

            if job.title:
                job._raw_text = text  # type: ignore[attr-defined]
                jobs.append(job)
        except Exception:
            continue
    return jobs


def _read_sensors(card) -> dict:
    """读取卡片 sensorsdata JSON。"""
    try:
        raw = card.attr("sensorsdata") or ""
        if not raw:
            inner = card.ele("css:[sensorsdata]", timeout=1)
            raw = inner.attr("sensorsdata") if inner else ""
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


# 51job 的 tag 混了技能和福利两类，靠关键词区分。
# 关键词要覆盖实际出现的写法：「周末双休」「全额五险一金」「法定假期」「加班调休」等。
_WELFARE_KW = (
    "五险", "一金", "公积金", "保险", "年假", "双休", "单休", "大小周",
    "体检", "餐", "补贴", "奖金", "期权", "股票", "股权", "培训", "旅游",
    "班车", "住宿", "宿舍", "节日", "假期", "法定", "假", "零食", "下午茶",
    "团建", "弹性", "调休", "福利", "关怀", "经费", "礼金", "慰问",
)


def _split_welfare(tags: list[str]) -> str:
    """从标签里挑出福利类，技能标签排除。"""
    out = []
    for t in tags:
        if any(k in t for k in _WELFARE_KW):
            out.append(t)
    return "、".join(dict.fromkeys(out))


def _enrich_detail(page, job: Job) -> None:
    """进详情页补 JD / 地址 / 公司简介 / 单双休 / 上下班。"""
    if not job.apply_url:
        return
    if not safe_get(page, job.apply_url, wait=3):
        return
    text = body_text(page)
    if not text:
        return

    # 单双休 & 上下班：优先看福利标签，再看 JD 正文
    job.work_rest = job.work_rest or parse_work_rest(f"{job.welfare} {text}")
    job.work_hours = parse_work_hours(text)

    # 职位描述：从"职位描述"到"工作地址"
    jd = _between(text, "职位描述", "工作地址")
    if jd:
        job.jd_text = jd[:1500]

    # 工作地址：优先用页面里的"地图完整地址："，它最干净
    m = re.search(r"地图完整地址：\s*([^\n]+)", text)
    if m:
        job.address = m.group(1).strip()[:120]
    else:
        addr = _between(text, "工作地址", "公司信息")
        if addr:
            # 地图组件会混进"点击查看地图 / 地图完整地址 / © 2026 Baidu"等噪声
            addr = re.split(r"点击查看地图|地图完整地址|©\s*\d{4}\s*Baidu", addr)[0]
            job.address = re.sub(r"\s+", " ", addr).strip()[:120]

    # 公司简介
    intro = text.split("公司信息", 1)[-1] if "公司信息" in text else ""
    if intro:
        job.company_intro = re.sub(r"\s+", " ", intro).strip()[:500]

    # 公司规模兜底
    m = re.search(r"(少于50人|50-150人|150-500人|500-1000人|1000-5000人|5000-10000人|10000人以上)", text)
    if m and not job.company_size:
        job.company_size = m.group(1)


def _between(text: str, start: str, end: str) -> str:
    i = text.find(start)
    if i < 0:
        return ""
    j = text.find(end, i + len(start))
    return text[i + len(start): j if j > 0 else i + 2000].strip()


def fetch(profile, max_pages: int = MAX_PAGE) -> list[Job]:
    """采集 51job 岗位。"""
    city_code = CITY_CODE.get(profile.city, "120300")
    seen: set[str] = set()
    out: list[Job] = []

    for kw in profile.keywords:
        for page_no in range(1, max_pages + 1):
            url = (
                f"{BASE}?jobArea={city_code}"
                f"&keyword={urllib.parse.quote(kw)}"
                f"&searchType=2&pageNum={page_no}"
            )
            # 始终取当前 page 对象：连接重建后旧引用会失效
            page = get_page()
            if not safe_get(page, url, wait=4.5):
                break
            batch = _parse_cards(get_page())
            if not batch:
                break
            new = 0
            for job in batch:
                key = (job.title + "|" + job.company)
                if key in seen:
                    continue
                seen.add(key)
                out.append(job)
                new += 1
            if new < 3:
                break
    return out


def enrich(jobs: list[Job], limit: int = 40) -> None:
    """对前 limit 条补详情（控制耗时）。"""
    for job in jobs[:limit]:
        page = get_page()
        _enrich_detail(page, job)
