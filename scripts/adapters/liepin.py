# -*- coding: utf-8 -*-
"""猎聘（liepin）适配器。

列表页卡片 `.jobDetailBox` 文本形如：
    岗位名 | 【青岛-市南区】 | 10-18k·15薪 | 3年以上 | 本科 | 公司名 | 行业融资规模
详情页补 JD / HR 活跃（"21小时前在线"）/ 公司介绍 / 地址。
城市用 dq 参数，青岛 = 250070。
"""

from __future__ import annotations

import re
import urllib.parse

from ..core.browser import body_text, get_page, safe_ele, safe_eles, safe_get
from ..core.models import Job
from ..core.scoring import parse_work_hours, parse_work_rest
from ..core import dom

PLATFORM = "liepin"

CITY_DQ = {"青岛": "250070", "济南": "250020", "北京": "010", "上海": "020",
           "深圳": "050090", "杭州": "070020", "成都": "280020"}
BASE = "https://www.liepin.com/zhaopin/"
MAX_PAGE = 2

# 经验+学历常被合并成一行，如「3年以上本科」「经验不限本科」「5-10年统招本科」
_EXP_ANY_RE = re.compile(
    r"(经验不限|应届|在校|实习|\d+\s*年以上|\d+-\d+\s*年|\d+\s*年以内|\d+\s*年)"
)
_EDU_ANY_RE = re.compile(r"(统招本科|本科|硕士|博士|大专|学历不限|中专|高中|初中)")


def _split_exp_edu(line: str) -> tuple[str, str] | None:
    """从一行里拆出 (经验, 学历)。两者都没有则返回 None。"""
    e = _EXP_ANY_RE.search(line)
    d = _EDU_ANY_RE.search(line)
    if not e and not d:
        return None
    return (e.group(1).strip() if e else "", d.group(1).strip() if d else "")


def _parse_card(card) -> Job | None:
    lines = [x.strip() for x in (card.text or "").split("\n") if x.strip()]
    if len(lines) < 3:
        return None

    job = Job(platform="猎聘")
    job.title = lines[0]

    a = dom.find(card, PLATFORM, "card_link", timeout=1)
    if a:
        href = a.attr("href") or ""
        job.apply_url = href.split("?")[0]

    for line in lines[1:]:
        m = re.match(r"^【(.+?)】$", line)
        if m and not job.city:
            job.city = m.group(1)
            continue
        if not job.salary and (re.search(r"\d[kK万].*(薪|/月)|\d+k$|面议", line) or "薪" in line):
            job.salary = line
            continue
        if not job.experience and not job.education:
            pair = _split_exp_edu(line)
            if pair:
                job.experience, job.education = pair
                continue
        if not job.company and not job.company_size and len(line) > 3 and not line.startswith("【"):
            # 公司行：名字 + 行业 + 融资 + 规模 常粘在一起，如「IT服务100-499人」
            if re.search(r"(人|以上)$", line):
                m2 = re.match(r"^(.*?)(\d[\d\-]*人.*|.*人以上)$", line)
                if m2:
                    job.company_industry = m2.group(1)
                    job.company_size = m2.group(2)
                continue
            job.company = line
            continue

    if not job.title:
        return None
    if not job.company:
        # 猎聘常隐藏雇主名，卡片可能只有「某知名公司」；真拿不到时标明
        job.company = "（猎聘未披露）"
    return job


def fetch(profile, max_pages: int = MAX_PAGE) -> list[Job]:
    dq = CITY_DQ.get(profile.city, "250070")
    seen: set[str] = set()
    out: list[Job] = []

    for kw in profile.keywords:
        for page_no in range(max_pages):
            url = f"{BASE}?key={urllib.parse.quote(kw)}&dq={dq}&currentPage={page_no}"
            page = get_page()
            if not safe_get(page, url, wait=4.5):
                break
            cards = dom.finds(page, PLATFORM, "card", timeout=5)
            if not cards:
                break
            new = 0
            for c in cards:
                try:
                    job = _parse_card(c)
                    if not job:
                        continue
                    key = job.title + "|" + job.company
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(job)
                    new += 1
                except Exception:
                    continue
            if new < 3:
                break
    return out


def _enrich_detail(page, job: Job) -> None:
    if not job.apply_url:
        return
    if not safe_get(page, job.apply_url, wait=4):
        return
    try:
        page.scroll.to_bottom()
        page.wait(1.5)
    except Exception:
        pass
    text = body_text(page)
    if not text:
        return

    # HR 活跃：形如「谢女士21小时前在线」「刘女士一周前在线」
    m = re.search(
        r"(\d+\s*(?:分钟|小时|天)前在线|一周前在线|刚刚在线|今日在线|本周在线|本周活跃)",
        text,
    )
    if m:
        job.hr_active = m.group(1)

    # HR 姓名 + 职位：「刘女士」直接跟在线状态或「聊一聊」
    m = re.search(r"([\u4e00-\u9fa5]{1,3})(先生|女士)", text)
    if m:
        job.hr_name = m.group(1) + m.group(2)

    # JD：优先用 [class*="job-intro"] 容器。
    # 不能按「职位介绍」关键字往后截 2000 字——页面底部有侧边栏推荐岗位，
    # 会把「Vue前端工程师（双休五险一金）」这类别的岗位标题混进来，
    # 导致福利和双休判断被污染。
    jd_box = dom.find(page, PLATFORM, "jd_box")
    if jd_box:
        seg = re.sub(r"\s+", " ", (jd_box.text or "")).strip()
        if seg:
            job.jd_text = seg[:1500]
    else:
        i = max(text.find("职位介绍"), text.find("岗位职责"), text.find("职位描述"))
        if i > 0:
            seg = text[i:i + 1200]
            # 截到推荐区之前
            cut = seg.find("聊一聊")
            if cut > 100:
                seg = seg[:cut]
            job.jd_text = re.sub(r"\s+", " ", seg)[:1500]

    # 单双休/福利只从 JD 里判断，不要用整页文本（会混入推荐岗位的标题）
    wf_status = _extract_welfare(job.jd_text or "")
    if wf_status:
        job.welfare = wf_status

    blob = f"{job.welfare} {job.jd_text or ''}"
    job.work_rest = job.work_rest or parse_work_rest(blob)
    job.work_hours = parse_work_hours(job.jd_text or text)

    # 公司信息区有固定字段前缀，比正则猜规模更准
    m = re.search(r"企业行业[：:]\s*([^\n|]{2,30})", text)
    if m and not job.company_industry:
        job.company_industry = m.group(1).strip()

    m = re.search(r"人数规模[：:]\s*(\d+-\d+人|少于\d+人|\d+人以上|10000人以上)", text)
    if m and not job.company_size:
        job.company_size = m.group(1)

    # 职位地址是猎聘最干净的地址来源
    m = re.search(r"职位地址[：:]\s*([^\n|]{6,80})", text)
    if m:
        job.address = m.group(1).strip()
    else:
        m = re.search(r"(工作地址|上班地址)[：:]?\s*([^\n]{6,80})", text)
        if m:
            job.address = m.group(2).strip()

    # 公司名兜底：卡片解析偶尔漏掉（公司行与标签粘连）
    if not job.company:
        head = text[:800]
        m = re.search(
            r"([\u4e00-\u9fa5A-Za-z0-9（）()·]{4,30}"
            r"(?:有限公司|股份有限公司|集团|科技|股份|研究院))",
            head,
        )
        if m:
            job.company = m.group(1).strip()

    # 行业兜底
    if not job.company_industry:
        m = re.search(
            r"(计算机软件|互联网|IT服务|人工智能|软件/IT服务|电子/半导体|"
            r"计算机服务|企业服务)",
            text[:1000],
        )
        if m:
            job.company_industry = m.group(1)


def enrich(jobs: list[Job], limit: int = 30) -> None:
    for job in jobs[:limit]:
        try:
            _enrich_detail(get_page(), job)
        except Exception:
            continue


# 福利关键词：猎聘不单独提供福利字段，只能从 JD 正文里找
_WELFARE_TOKENS = (
    "五险一金", "六险一金", "五险", "公积金", "补充医疗", "商业医疗保险",
    "年终奖", "年终奖金", "绩效奖金", "项目奖金", "股票期权", "期权", "股权激励",
    "带薪年假", "年假", "带薪病假", "法定节假日", "法定假期", "节日福利",
    "定期体检", "年度体检", "体检", "员工旅游", "团建", "团队活动",
    "餐补", "餐饮补贴", "交通补贴", "通讯补贴", "住房补贴", "高温补贴",
    "加班费", "加班补助", "调休", "弹性工作", "弹性工作制", "周末双休",
    "双休", "十三薪", "14薪", "免费班车", "班车", "员工宿舍", "住宿",
    "零食", "下午茶", "免费工作餐", "全勤奖", "员工关怀", "礼金",
)


def _extract_welfare(text: str) -> str:
    """从 JD 正文里提取福利项。

    猎聘没有独立的福利字段，福利散在「福利待遇」段或职位标题的括注里
    （如「Vue前端工程师（双休五险一金）」）。用关键词表扫，并做子串去重。
    """
    if not text:
        return ""
    found = [t for t in _WELFARE_TOKENS if t in text]
    out: list[str] = []
    for t in sorted(set(found), key=len, reverse=True):
        if not any(t != o and t in o for o in out):
            out.append(t)
    return "、".join(out[:10])
