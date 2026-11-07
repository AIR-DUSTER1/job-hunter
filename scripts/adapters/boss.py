# -*- coding: utf-8 -*-
"""BOSS 直聘（zhipin.com）适配器。

BOSS 未登录时列表页不返回卡片（会被安全校验拦截），因此必须先登录
（`python -m scripts.main login boss`）。登录后卡片 `.job-card-wrap` 文本形如：
    岗位名 / 薪资 / 城市·区 / 经验 / 学历 / 公司名 / 规模·融资·行业
详情页 job_detail 补 JD、地址、公司简介、HR 活跃。
城市青岛 = 101120200。

选择器统一从 `selectors.json` 的 `platforms.boss` 取（经 `core.dom`），
站点改版时先用 `python -m scripts.main diagnose --platforms boss` 定位，不要直接改这里。
"""

from __future__ import annotations

import re
import urllib.parse

from ..core.browser import body_text, get_page, safe_ele, safe_eles, safe_get
from ..core.models import Job
from ..core.scoring import parse_work_hours, parse_work_rest
from ..core import dom
from ..core.health import StructureChanged

PLATFORM = "boss"

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


# BOSS 会在 JD 正文里随机插入站点名做水印，位置在词中间（如「流BOSS直聘程编排」），
# 所以要在去空白之后按子串删除，而不是只删完整短语。
_WATERMARKS = ("来自BOSS直聘", "BOSS直聘", "直聘")


def _clean_watermark(text: str) -> str:
    out = text
    for w in _WATERMARKS:
        out = out.replace(w, "")
    return out



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
        # 登录态标志：个人入口头像（候选链见 selectors.json）
        if dom.find(page, PLATFORM, "logged_in", timeout=1):
            return True
        # 兜底：登录/注册按钮在场即未登录
        if dom.find(page, PLATFORM, "unlogged_marker", timeout=1):
            return False
        return False
    except Exception:
        return False


def _parse_card(card) -> Job | None:
    """卡片文本形态：岗位名·薪资|经验|学历|公司名 城市·区·商圈"""
    job = Job(platform="BOSS直聘")

    # 岗位名：优先 .job-name / 带 title 属性的元素（候选链见 selectors.json）
    t = dom.find(card, PLATFORM, "card_title", timeout=1)
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
    s = dom.find(card, PLATFORM, "card_salary", timeout=1)
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
        footer = dom.find(card, PLATFORM, "card_footer", timeout=1)
        if footer:
            ft = (footer.text or "").strip()
            job.company = ft.split("\n")[0].split(" ")[0][:30]

    # 详情链接：href 可能是绝对地址，也可能是 /job_detail/xxx 相对地址
    a = dom.find(card, PLATFORM, "card_link", timeout=1)
    if a:
        href = (a.attr("href") or "").split("?")[0]
        if href.startswith("http"):
            job.apply_url = href
        elif href.startswith("/"):
            job.apply_url = "https://www.zhipin.com" + href

    # 公司主页链接：补公司规模/行业/融资要用
    cl = dom.find(card, PLATFORM, "card_company_link", timeout=1)
    if cl:
        ch = (cl.attr("href") or "").split("?")[0]
        if ch.startswith("/"):
            ch = "https://www.zhipin.com" + ch
        if ch.startswith("http"):
            job.company_url = ch

    # 福利标签
    wel = dom.finds(card, PLATFORM, "card_tags")
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
            cards = dom.finds(page, PLATFORM, "card", timeout=5)
            if not cards:
                if not is_logged_in(page):
                    blocked = True
                break
            new = 0
            batch: list[Job] = []
            for c in cards:
                try:
                    job = _parse_card(c)
                    if not job:
                        continue
                    batch.append(job)
                except Exception:
                    continue

            # 虚拟列表会周期性重渲染，首轮可能有一批卡片读不到薪资（.job-salary
            # 元素已失效）。薪资是打分的关键项，整批缺失时重取一次再解析。
            if batch and not any(j.salary for j in batch):
                for c in dom.finds(page, PLATFORM, "card", timeout=5):
                    try:
                        job = _parse_card(c)
                        if job:
                            batch.append(job)
                    except Exception:
                        continue

            for job in batch:
                key = job.title + "|" + job.company
                if key in seen:
                    continue
                seen.add(key)
                out.append(job)
                new += 1
            if new < 3:
                break
        if blocked:
            break

    # 只在完全拿不到数据时才报错；拿到部分数据（哪怕 1 条）说明是真实列表，
    # 后续翻页被拦属于正常限流，保留已有结果。
    #
    # 抛 StructureChanged 而不是裸 RuntimeError：它仍是 RuntimeError 子类，
    # collect.py 原有分支继续生效，同时能单独捕获做自愈重试。
    if blocked and not out:
        raise StructureChanged(
            PLATFORM,
            "未登录或被安全校验拦截，卡片数为 0。请先运行：python -m scripts.main login boss",
        )
    return out


def _enrich_by_click(page, job: Job, cards: list) -> bool:
    """点击列表卡片，读右侧详情面板。

    比直接打开 job_detail 页可靠：详情页有独立的反爬校验，经常被重定向回首页；
    而列表页点卡片只是在当前 SPA 里切换面板，不会触发跳转。

    面板里能拿到：JD、工作地址、HR 姓名与活跃状态（.boss-active-time）。
    """
    card = _find_card(cards, job)
    if card is None:
        return False
    try:
        card.click()
    except Exception:
        return False
    page.wait(2.5)

    box = dom.find(page, PLATFORM, "detail_panel", timeout=1)
    text = (box.text if box else "") or body_text(page)
    if not text or ("职位描述" not in text and "岗位职责" not in text):
        return False

    # HR 活跃：优先用精确选择器，其次文本
    at = dom.find(page, PLATFORM, "detail_active", timeout=1)
    if at:
        job.hr_active = (at.text or "").strip()
    else:
        m = re.search(r"(刚刚活跃|今日活跃|本周活跃|\d+\s*(?:分钟|小时|天)前活跃)", text)
        if m:
            job.hr_active = m.group(1)

    # HR 姓名：「郭女士」「陈先生」通常紧跟在活跃状态前
    m = re.search(r"([\u4e00-\u9fa5]{1,3})(先生|女士)", text)
    if m and not job.hr_name:
        job.hr_name = m.group(1) + m.group(2)

    job.work_rest = job.work_rest or parse_work_rest(f"{job.welfare} {text}")
    job.work_hours = job.work_hours or parse_work_hours(text)

    i = max(text.find("职位描述"), text.find("岗位职责"))
    if i > 0:
        seg = _clean_watermark(re.sub(r"\s+", " ", text[i:i + 2000]))
        if len(seg) > len(job.jd_text or ""):
            job.jd_text = seg[:1500]

    m = re.search(r"(工作地址)[：:]?\s*\n?\s*([^\n]{5,90})", text)
    if m:
        addr = re.sub(r"\s+", " ", m.group(2)).strip()
        if "点击查看地图" not in addr:
            job.address = addr[:120]
    return True


def _find_card(cards: list, job: Job):
    """在卡片列表里按岗位名 + 公司名定位。"""
    want_t = (job.title or "")[:8]
    want_c = (job.company or "")[:6]
    for c in cards:
        t = decode_obfuscated((c.text or "").replace("\n", ""))
        if want_t and want_t in t and (not want_c or want_c in t):
            return c
    return None


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
        seg = _clean_watermark(seg)
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


def _enrich_company(page, job: Job) -> bool:
    """进公司主页补规模 / 行业 / 融资 / 公司简介。

    BOSS 列表卡片只有岗位名、薪资、经验、学历、公司名、地区，规模和行业
    只在公司页（/gongsi/xxx）上，那里有一行固定格式：
        「未融资 20-99人 电子商务」
    依次是 融资阶段 / 规模 / 行业。
    """
    url = getattr(job, "company_url", "")
    if not url:
        return False
    if not safe_get(page, url, wait=4):
        return False
    try:
        if "gongsi" not in (page.url or ""):
            return False
    except Exception:
        return False

    text = body_text(page)
    if not text:
        return False

    # 三元组：融资 + 规模 + 行业
    m = re.search(
        r"(未融资|不需要融资|天使轮|A轮|B轮|C轮|D轮|E轮|已上市|战略融资|融资未公开|IPO上市)"
        r"\s*(\d+-\d+人|少于\d+人|\d+人以上|\d+人)"
        r"\s*([\u4e00-\u9fa5A-Za-z/、,+ ]{2,20})",
        text,
    )
    if m:
        job.company_financing = job.company_financing or m.group(1)
        job.company_size = job.company_size or m.group(2)
        if not job.company_industry:
            ind = m.group(3).strip()
            # 截掉可能粘连的下一个字段
            job.company_industry = ind.split("公司简介")[0].strip()[:20]

    # 公司简介：.job-sec 里的「公司简介」段
    sec = dom.find(page, PLATFORM, "company_sec")
    if sec:
        seg = (sec.text or "").replace("公司简介", "", 1).strip()
        seg = re.sub(r"\s+", " ", seg)
        if len(seg) > 20:
            job.company_intro = seg[:500]

    # 「工作时间及福利」区块：BOSS 独有的结构化福利来源
    wt = dom.find(page, PLATFORM, "company_worktime")
    if wt:
        _read_work_time(wt, job)

    # 上架时间：公司页顶部会写「职位列表第一个职位更新时间:2026-10-29」
    m = re.search(r"职位列表第一个职位更新时间[：:]\s*(\d{4}-\d{2}-\d{2})", text)
    if m and not job.published:
        job.published = m.group(1)

    return True


def _read_work_time(block, job: Job) -> None:
    """解析「工作时间及福利」区块。

    区块文本形如：
        工作时间及福利
        上午09:00 - 下午06:00
        不加班|交通补助|生日福利|节日福利|通讯补贴|有无线网
        团建聚餐|零食下午茶|员工旅游|带薪年假|全勤奖|绩效奖金|年终奖|五险一金

    第一行是标题、第二行是作息、其余全是福利项。这是 BOSS 唯一成体系
    披露福利与工时的地方，公司页比岗位详情页更值得抓。
    """
    lines = [x.strip() for x in (block.text or "").split("\n") if x.strip()]
    if not lines:
        return
    if lines and "工作时间" in lines[0]:
        lines = lines[1:]

    for line in lines:
        # 作息行：上午09:00 - 下午06:00
        if re.search(r"\d{1,2}:\d{2}\s*[-~至]\s*", line):
            job.work_hours = job.work_hours or _normalize_hours(line)
            continue
        # 福利：可能是单行一项，也可能一行多项用 | 或空格分隔
        for tok in re.split(r"[|｜、,，\s]+", line):
            tok = tok.strip("·•-— ")
            if 2 <= len(tok) <= 12 and not tok.isdigit():
                if tok not in job.welfare:
                    job.welfare = (job.welfare + "、" + tok) if job.welfare else tok

    job.work_rest = job.work_rest or parse_work_rest(job.welfare)


def _normalize_hours(line: str) -> str:
    """把「上午09:00 - 下午06:00」规整成「09:00-18:00」。"""
    m = re.search(
        r"(上午|下午|早上|晚上)?\s*(\d{1,2}):(\d{2})\s*[-~至]\s*"
        r"(上午|下午|早上|晚上)?\s*(\d{1,2}):(\d{2})",
        line,
    )
    if not m:
        return ""
    p1, h1, m1, p2, h2, m2 = m.groups()
    a, b = int(h1), int(h2)
    if p2 in ("下午", "晚上") and b < 12:
        b += 12
    if p1 in ("下午", "晚上") and a < 12:
        a += 12
    return f"{a:02d}:{m1}-{b:02d}:{m2}"


def enrich(jobs: list[Job], limit: int = 30) -> None:
    """补详情：点击卡片读面板（JD/地址/HR活跃），再补公司页（规模/行业/融资）。

    点击面板是主路径——详情页有独立反爬，经常被重定向；面板只是 SPA 内切换。
    一页只有 15 张卡片，而清单可能有几十条，所以每条都要按岗位名重新搜一次，
    否者后面大半都找不到对应卡片。

    公司页按公司名去重，同公司多个岗位只查一次。
    """
    import urllib.parse
    import time

    page = get_page()
    targets = jobs[:limit]
    if not targets:
        return

    # 从岗位城市推断搜索城市（「济南·历下区」→ 济南），未知时退回青岛
    city_name = next((re.split(r"[·-]", (j.city or "").strip())[0]
                      for j in jobs if (j.city or "").strip()), "")
    city = CITY_CODE.get(city_name, "101120200")

    # 第一轮：逐条搜索 → 点卡片 → 读面板
    done = 0
    misses = 0
    for job in targets:
        kw = urllib.parse.quote((job.title or "")[:12])
        if not safe_get(page, f"{BASE}?query={kw}&city={city}", wait=4.5):
            misses += 1
            if misses >= 4:
                print("    BOSS 搜索页连续失败，停止补全", flush=True)
                break
            continue

        cards = dom.finds(page, PLATFORM, "card", timeout=5)
        if _enrich_by_click(page, job, cards):
            done += 1
            misses = 0
        elif _enrich_detail(page, job):
            done += 1
            misses = 0
        else:
            misses += 1
            if misses >= 5:
                print("    BOSS 详情连续未命中，停止补全", flush=True)
                break
            time.sleep(3)

    if done:
        print(f"    BOSS 岗位详情补全 {done} 条", flush=True)

    # 第二轮：公司页补规模/行业/融资，按公司去重
    seen: set[str] = set()
    company_hits = 0
    company_misses = 0
    for job in jobs[:limit]:
        if job.company_size and job.company_industry:
            continue
        name = job.company or ""
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            if _enrich_company(page, job):
                company_hits += 1
                company_misses = 0
                # 同一公司的其他岗位共享这些信息
                for other in jobs:
                    if other.company == name:
                        other.company_size = other.company_size or job.company_size
                        other.company_industry = other.company_industry or job.company_industry
                        other.company_financing = other.company_financing or job.company_financing
                        other.company_intro = other.company_intro or job.company_intro
            else:
                company_misses += 1
                if company_misses >= 3:
                    print("    公司页连续 3 次未命中，停止补全", flush=True)
                    break
                time.sleep(3)
        except Exception:
            company_misses += 1
            if company_misses >= 3:
                break

    if company_hits:
        print(f"    公司页补全 {company_hits} 家公司", flush=True)
