# -*- coding: utf-8 -*-
"""BOSS 直聘（zhipin.com）适配器。

BOSS 未登录时列表页不返回卡片（会被安全校验拦截），因此必须先登录
（`python -m scripts.main login boss`）。登录后卡片 `.job-card-wrap` 文本形如：
    岗位名 / 薪资 / 城市·区 / 经验 / 学历 / 公司名 / 规模·融资·行业
详情页 job_detail 补 JD、地址、公司简介、HR 活跃。
城市青岛 = 101120200。
"""

from __future__ import annotations

import re
import urllib.parse

from ..core.browser import body_text, get_page, safe_ele, safe_eles, safe_get
from ..core.models import Job
from ..core.scoring import parse_work_hours, parse_work_rest

CITY_CODE = {"青岛": "101120200", "济南": "101120100", "北京": "101010100",
             "上海": "101020100", "深圳": "101280600", "杭州": "101210100",
             "成都": "101270100"}
BASE = "https://www.zhipin.com/web/geek/jobs"
DETAIL = "https://www.zhipin.com{}"
MAX_PAGE = 2

_SALARY_RE = re.compile(r"\d+(?:\.\d+)?-\d+(?:\.\d+)?[Kk]·?\d*薪?|面议")
_SALARY_ANY = re.compile(r"\d+(?:\.\d+)?-\d+(?:\.\d+)?(?:[Kk]|元/[月天])")
_SALARY_SPLIT = re.compile(r"\d+(?:\.\d+)?-\d+(?:\.\d+)?[Kk]")
# 标题尾部粘着薪资碎片：可能是完整薪资，也可能是被截断的「…4000-」「…1」
_TITLE_TAIL = re.compile(r"[-\s]*\d[\d.\-]*\s*(?:[Kk]|元/月|元/天)?\s*$")
_EXP_RE = re.compile(r"^(经验不限|应届|\d+-\d+年|\d+年以上|在校/应届)$")
_EDU_RE = re.compile(r"^(本科|硕士|博士|大专|学历不限|中专/中技|高中)$")


def decode_obfuscated(text: str) -> str:
    """解码 BOSS 的字体混淆数字。

    BOSS 把薪资数字替换成 Unicode 私有区字符：U+E031 表示 0，U+E032 表示 1，
    依次到 U+E039 表示 8（偏移量为 0xE031）。该映射在多次页面加载间保持稳定，
    已在连续加载中验证。其余字符（K、元/月、-）原样保留。
    """
    if not text:
        return text
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0xE030 <= code <= 0xE03F:
            digit = code - 0xE031
            out.append(str(digit) if 0 <= digit <= 9 else "?")
        else:
            out.append(ch)
    return "".join(out)


def is_logged_in(page) -> bool:
    """BOSS 登录后右上角出现用户头像入口（/web/geek/recommend 的头像图）。

    仅凭「没有登录/注册」不可靠：安全校验页同样不含该字样。
    """
    try:
        # 登录态标志：个人入口头像
        if safe_eles(page, 'xpath://a[@href="https://www.zhipin.com/web/geek/recommend"]//img', timeout=1):
            return True
        if safe_eles(page, "css:.nav-figure, .user-nav .figure, .header-user", timeout=1):
            return True
        # 兜底：登录/注册按钮在场即未登录
        if safe_eles(page, "css:.header-login-btn, .btn-outline", timeout=1):
            return False
        return False
    except Exception:
        return False


def _parse_card(card) -> Job | None:
    """卡片文本形态：岗位名·薪资|经验|学历|公司名 城市·区·商圈"""
    job = Job(platform="BOSS直聘")

    # 岗位名：优先 .job-name / 带 title 属性的元素
    t = card.ele("css:.job-name, .job-title, [class*='job-name']", timeout=1)
    if t:
        job.title = (t.text or "").strip()
    if not job.title:
        # 退路：卡片第一行，形如「AI Agent开发工程师10-15K」
        first = (card.text or "").split("\n")[0]
        job.title = _SALARY_SPLIT.split(first)[0].strip()
    # 标题里也可能混着混淆数字（如「AI Agent开发工程师-15K」），清理掉
    job.title = decode_obfuscated(job.title)
    job.title = _TITLE_TAIL.sub("", job.title).strip()
    if not job.title:
        return None

    # 薪资：需要解码字体混淆
    s = card.ele("css:.job-salary, [class*='salary']", timeout=1)
    if s:
        job.salary = decode_obfuscated((s.text or "").strip())

    # 卡片正文各段
    raw_lines = [x.strip() for x in (card.text or "").split("\n") if x.strip()]
    dec_lines = [decode_obfuscated(x) for x in raw_lines]

    for v in dec_lines:
        if not job.salary and _SALARY_ANY.search(v):
            job.salary = _SALARY_ANY.search(v).group(0)
            continue
        if not job.experience and _EXP_RE.match(v):
            job.experience = v
            continue
        if not job.education and _EDU_RE.match(v):
            job.education = v
            continue
        # 「公司名 城市·区·商圈」：先匹配这个组合形态，一次拿全公司+地区
        m = re.match(r"^([\u4e00-\u9fa5A-Za-z0-9（）()]{2,26})\s+([\u4e00-\u9fa5]{2,8}·.+)$", v)
        if m:
            if not job.company:
                job.company = m.group(1).strip()
            if not job.city:
                job.city = m.group(2).strip()
            continue
        # 纯地区行
        if not job.city and "·" in v and len(v) < 40:
            job.city = v
            continue

    if not job.company:
        footer = card.ele("css:.job-card-footer, [class*='company-name']", timeout=1)
        if footer:
            ft = (footer.text or "").strip()
            job.company = ft.split("\n")[0].split(" ")[0][:30]

    # 详情链接：href 可能是绝对地址，也可能是 /job_detail/xxx 相对地址
    a = card.ele('css:a[href*="/job_detail"]', timeout=1)
    if a:
        href = (a.attr("href") or "").split("?")[0]
        if href.startswith("http"):
            job.apply_url = href
        elif href.startswith("/"):
            job.apply_url = "https://www.zhipin.com" + href

    # 福利标签
    wel = card.eles("css:.tag-list span, [class*='tag-list'] span")
    tags = [decode_obfuscated((w.text or "").strip()) for w in wel if w.text]
    welfare = [x for x in tags if any(k in x for k in ("五险", "双休", "年假", "补贴", "奖金", "公积金"))]
    if welfare:
        job.welfare = "、".join(dict.fromkeys(welfare))

    return job


def fetch(profile, max_pages: int = MAX_PAGE) -> list[Job]:
    city = CITY_CODE.get(profile.city, "101120200")
    seen: set[str] = set()
    out: list[Job] = []
    blocked = False

    for kw in profile.keywords:
        for page_no in range(1, max_pages + 1):
            url = (
                f"{BASE}?query={urllib.parse.quote(kw)}"
                f"&city={city}&page={page_no}"
            )
            page = get_page()
            if not safe_get(page, url, wait=5):
                break
            try:
                cur_url = page.url or ""
            except Exception:
                break
            if "_security_check" in cur_url or "verify" in cur_url:
                blocked = True
                break
            page = get_page()
            cards = safe_eles(page, "css:.job-card-wrap")
            if not cards:
                if not is_logged_in(page):
                    blocked = True
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
        if blocked:
            break

    # 只在完全拿不到数据时才报错；拿到部分数据（哪怕 1 条）说明是真实列表，
    # 后续翻页被拦属于正常限流，保留已有结果。
    if blocked and not out:
        raise RuntimeError(
            "BOSS 直聘未登录或被安全校验拦截。请先运行：python -m scripts.main login boss"
        )
    return out


def _enrich_detail(page, job: Job) -> bool:
    """补全单条详情。返回 False 表示页面没落在岗位详情上（被重定向/拦截）。"""
    if not job.apply_url:
        return False
    if not safe_get(page, job.apply_url, wait=4):
        return False

    # 校验落地页：被重定向回首页或落到校验页时，说明触发了限流
    try:
        cur = page.url or ""
    except Exception:
        return False
    if "job_detail" not in cur:
        return False

    text = body_text(page)
    if not text or "职位描述" not in text and "岗位职责" not in text:
        return False

    # 上架时间：「发布于 3天前」「今日发布」等
    m = re.search(r"(发布于|更新于)\s*(\d+\s*(?:分钟|小时|天|周|个月)前|今天|今日|昨天)", text)
    if m:
        job.published = m.group(2)
    else:
        m = re.search(r"(\d+\s*(?:分钟|小时|天|周)前(?:发布|更新))", text)
        if m:
            job.published = m.group(1)

    # HR 活跃：「今日活跃」「3小时前活跃」「本周活跃」「刚刚活跃」
    m = re.search(
        r"([\u4e00-\u9fa5]{1,3})(先生|女士)?\s*"
        r"(刚刚活跃|今日活跃|本周活跃|\d+\s*(?:分钟|小时|天)前活跃|今日在线|本周在线)",
        text,
    )
    if m:
        job.hr_name = job.hr_name or (m.group(1) + (m.group(2) or ""))
        job.hr_active = m.group(3)

    job.work_rest = job.work_rest or parse_work_rest(text)
    job.work_hours = job.work_hours or parse_work_hours(text)

    i = max(text.find("职位描述"), text.find("岗位职责"))
    if i > 0:
        seg = text[i:i + 2200]
        # 去掉正文里的站点水印字样
        seg = seg.replace("来自BOSS直聘", "")
        job.jd_text = re.sub(r"\s+", " ", seg)[:1500]

    m = re.search(r"(工作地址)[：:]?\s*\n?\s*([^\n]{5,90})", text)
    if m:
        addr = re.sub(r"\s+", " ", m.group(2)).strip()
        if "点击查看地图" not in addr:
            job.address = addr[:120]

    for kw in ("公司介绍", "公司简介", "工商信息"):
        i = text.find(kw)
        if i > 0:
            seg = text[i + len(kw): i + len(kw) + 700]
            seg = re.sub(r"\s+", " ", seg).strip()
            if len(seg) > 20:
                job.company_intro = seg[:500]
                break

    if not job.company_size:
        m = re.search(
            r"(少于15人|15-50人|50-99人|100-499人|500-999人|1000-9999人|10000人以上)",
            text,
        )
        if m:
            job.company_size = m.group(1)
    return True


def enrich(jobs: list[Job], limit: int = 30) -> None:
    """逐条补详情；连续失败 3 次说明被限流，提前停止而不是空转。"""
    import time

    done = 0
    misses = 0
    for job in jobs[:limit]:
        try:
            if _enrich_detail(get_page(), job):
                done += 1
                misses = 0
            else:
                misses += 1
                if misses >= 3:
                    print("    详情页连续 3 次未命中，疑似限流，停止补全", flush=True)
                    break
                time.sleep(4)
        except Exception:
            misses += 1
            if misses >= 3:
                break
