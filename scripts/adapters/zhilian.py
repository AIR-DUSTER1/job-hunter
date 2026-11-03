# -*- coding: utf-8 -*-
"""智联招聘（zhaopin.com）适配器。

列表页 `https://www.zhaopin.com/sou/jl703/kw<编码>/p<n>` 按城市+关键词检索。
卡片结构以 `[class*="joblist-box__item"]` 定位，文本用换行分段：
    岗位名 / 薪资 / 城市·区 / 经验 / 学历 / 公司名 / 规模 / 行业 / HR·职位 / 活跃度
青岛 jl 编码 = 703。
"""

from __future__ import annotations

import re

from ..core.browser import body_text, get_page, safe_ele, safe_eles, safe_get
from ..core.models import Job
from ..core.scoring import parse_work_hours, parse_work_rest
from ..core import dom
from ..core import selectors

PLATFORM = "zhilian"

CITY_JL = {"青岛": "703", "济南": "702", "北京": "530", "上海": "538",
           "深圳": "765", "杭州": "653", "成都": "801"}
BASE = "https://www.zhaopin.com/sou/jl{jl}/p{pno}?kw={kw}"
MAX_PAGE = 2

_SALARY_RE = re.compile(r"^(\d+(?:\.\d+)?-\d+(?:\.\d+)?[万千](?:元)?|面议)")
_EXP_RE = re.compile(r"(经验不限|应届|\d+\s*年以上|\d+-\d+\s*年|\d+\s*年以内)")
_EDU_RE = re.compile(r"(本科|硕士|博士|大专|中专|中技|高中|学历不限)")
_SIZE_RE = re.compile(r"(\d+-\d+人|少于\d+人|\d+人以上|10000人以上)")
# 只含经验/学历的干净行，如「大专 3-5年」「3-5年 本科」
_EXP_EDU_ONLY = re.compile(
    r"^[\s　]*(?:(?:经验不限|应届|\d+\s*年以上|\d+-\d+\s*年|\d+\s*年以内|在校|实习)"
    r"[\s　]*)?(?:(?:本科|硕士|博士|大专|中专|中技|高中|学历不限)[\s　]*)?$"
)
# 公司名后缀：用于从混杂的技能行里挑出真正的公司名
_COMPANY_SUFFIX = re.compile(
    r"(有限公司|股份有限公司|集团公司|集团|科技|信息技术|软件|网络|研究院|研究所|"
    r"银行|证券|保险|大学|医院|中心|事务所|工作室|厂|股份)$"
)
# 形如「青岛 崂山 中韩」的地区行，避免被兜底误判成公司
_CITY_ONLY = re.compile(r"^[\u4e00-\u9fa5]{2,6}\s+[\u4e00-\u9fa5]{2,6}")


# 从 Vue 组件实例读取岗位列表的 JS。
#
# 为什么不用卡片文本：智联的卡片是纯展示 DOM，不含 jobId / 详情链接 / 发布时间，
# 而完整字段都挂在 Vue 组件的 list prop 上。直接读这个对象能拿到
# positionURL（投递链接）、publishTime（上架时间）、welfareLabel（福利）、
# jobDescription（JD 摘要）等，比解析文本准确得多。
_READ_LIST_JS = """
return (function(){
  var sel = arguments[0] || ".job-card";
  var card = document.querySelector(sel);
  if (!card) return null;
  // list prop 挂在卡片的直接父级组件上
  var host = card.parentElement;
  var vm = host && host.__vue__;
  if (!vm) {
    // 兜底：向上多找几层
    var n = host, i = 0;
    while (n && i < 6) {
      if (n.__vue__ && n.__vue__.$props && n.__vue__.$props.list) { vm = n.__vue__; break; }
      n = n.parentElement; i++;
    }
  }
  var list = (vm && vm.$props && vm.$props.list) || [];
  if (!list.length) return null;
  return list.map(function(x){
    var tags = (x.jobSkillTags || []).map(function(t){ return t && t.name; }).filter(Boolean);
    var welfare = (x.welfareLabel || []).map(function(w){
      return (typeof w === "string") ? w : (w && (w.name || w.label));
    }).filter(Boolean);
    return {
      name: x.name,
      jobId: x.jobId,
      url: x.positionURL || x.positionUrl || "",
      publish: x.publishTime || x.firstPublishTime || "",
      salary60: x.salary60 || "",
      salaryReal: x.salaryReal || "",
      company: x.companyName || "",
      size: x.companySize || "",
      industry: x.industryName || "",
      financing: x.financingStage || "",
      city: x.workCity || "",
      district: x.cityDistrict || "",
      exp: x.workingExp || "",
      edu: x.education || "",
      skills: tags,
      welfare: welfare,
      jd: x.jobDescription || "",
      workDateType: x.workDateType || ""
    };
  });
})()
"""


def _read_vue_list(page) -> list[dict]:
    """读取页面 Vue 数据里的岗位列表。失败返回空列表。

    卡片选择器从注册表取（`vue_host`），取不到时退回候选链首选。
    """
    try:
        chain = selectors.candidates(PLATFORM, "vue_host")
    except selectors.SelectorRegistryError:
        chain = ["css:.job-card"]
    raw = chain[0] if chain else "css:.job-card"
    # 注册表里存的是 "css:.job-card"，JS 的 querySelector 只吃后面的部分
    host_sel = raw.split(":", 1)[1] if raw.startswith("css:") else raw

    try:
        data = page.run_js(_READ_LIST_JS, host_sel)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _job_from_dict(rec: dict) -> Job | None:
    """把 Vue 列表项转成 Job。"""
    title = (rec.get("name") or "").strip()
    if not title:
        return None

    job = Job(platform="智联招聘")
    job.title = title
    job.company = (rec.get("company") or "").strip() or "（智联未披露）"

    # 薪资：优先 salary60（「1.2-2.2万」），其次 salaryReal（「12001-22000」）
    sal = (rec.get("salary60") or "").strip()
    if not sal:
        real = (rec.get("salaryReal") or "").strip()
        m = re.match(r"(\d+)-(\d+)$", real)
        if m:
            lo, hi = int(m.group(1)) / 1000, int(m.group(2)) / 1000
            sal = f"{lo:g}-{hi:g}K"
    job.salary = sal

    job.apply_url = (rec.get("url") or "").strip()
    job.published = (rec.get("publish") or "").strip()
    job.experience = (rec.get("exp") or "").strip()
    job.education = (rec.get("edu") or "").strip()
    job.company_size = (rec.get("size") or "").strip()
    job.company_industry = (rec.get("industry") or "").strip()
    job.company_financing = (rec.get("financing") or "").strip()

    city = (rec.get("city") or "").strip()
    district = (rec.get("district") or "").strip()
    job.city = f"{city}·{district}" if city and district else city

    welfare = rec.get("welfare") or []
    if welfare:
        job.welfare = "、".join(dict.fromkeys(welfare))

    jd = (rec.get("jd") or "").strip()
    if jd:
        job.jd_text = re.sub(r"\s+", " ", jd)[:1500]

    # 单双休/工时：福利标签 + JD 一起找
    blob = f"{job.welfare} {jd}"
    job.work_rest = parse_work_rest(blob)
    job.work_hours = parse_work_hours(blob)

    return job


def _parse_card(card) -> Job | None:
    lines = [x.strip() for x in (card.text or "").split("\n") if x.strip()]
    if len(lines) < 3:
        return None

    job = Job(platform="智联招聘")
    job.title = lines[0]
    rest = lines[1:]

    # 逐项用特征识别，不再依赖固定顺序
    for line in rest:
        # 薪资：含数字+单位，或未登录时的遮蔽形态 **-**元
        if not job.salary and (
            re.search(r"\d\s*-?\s*\d*\s*(万|千|K|k|元)", line)
            or re.fullmatch(r"\*+\s*-\s*\*+\s*元", line)
        ):
            job.salary = line
            continue
        # 经验 + 学历可能在同一行
        e = _EXP_RE.search(line)
        d = _EDU_RE.search(line)
        if (e or d) and not (job.experience and job.education):
            if e and not job.experience:
                job.experience = e.group(1)
            if d and not job.education:
                job.education = d.group(1)
            # 该行若只剩经验/学历则跳过；否则可能还带技能，继续但不当作公司
            if _EXP_EDU_ONLY.fullmatch(line):
                continue
        # 地区：形如「青岛 崂山 中韩」
        if not job.city and re.match(r"^[\u4e00-\u9fa5]{2,6}\s+[\u4e00-\u9fa5]{2,6}", line):
            job.city = line
            continue
        # 公司：以公司类后缀结尾，且不含技能分隔符
        if not job.company and _COMPANY_SUFFIX.search(line) and len(line) <= 30:
            job.company = line
            continue
        # 兜底：无后缀的短名（如「中软国际」「澳柯玛」）——纯中文、无技能/标点
        if (
            not job.company
            and 2 <= len(line) <= 12
            and re.fullmatch(r"[\u4e00-\u9fa5A-Za-z]+", line)
            and not _EXP_RE.fullmatch(line)
            and not _EDU_RE.fullmatch(line)
            and not _CITY_ONLY.match(line)
        ):
            job.company = line
            continue

    if not job.title:
        return None
    return job


def _collect_page(page) -> list[Job]:
    """取一页岗位：优先读 Vue 数据（字段全、带链接），失败再退回解析卡片文本。"""
    recs = _read_vue_list(page)
    if recs:
        jobs = []
        for rec in recs:
            job = _job_from_dict(rec)
            if job:
                jobs.append(job)
        if jobs:
            return jobs

    # 兜底：卡片文本解析（无链接、无发布时间）
    jobs = []
    for card in dom.finds(page, PLATFORM, "card", timeout=5):
        try:
            job = _parse_card(card)
            if job:
                jobs.append(job)
        except Exception:
            continue
    return jobs


def fetch(profile, max_pages: int = MAX_PAGE) -> list[Job]:
    jl = CITY_JL.get(profile.city, "703")
    seen: set[str] = set()
    out: list[Job] = []

    for kw in profile.keywords:
        for pno in range(1, max_pages + 1):
            url = BASE.format(jl=jl, pno=pno, kw=kw)
            if not safe_get(get_page(), url, wait=4.5):
                break
            batch = _collect_page(get_page())
            if not batch:
                break
            new = 0
            for job in batch:
                key = job.title + "|" + job.company
                if key in seen:
                    continue
                seen.add(key)
                out.append(job)
                new += 1
            if new < 3:
                break
    return out


def enrich(jobs: list[Job], limit: int = 20) -> None:
    """补公司介绍与福利。

    智联的数据分两处：
    - 列表页 Vue 数据：岗位、薪资、经验、学历、公司名、规模、行业、融资、
      上架时间、投递链接、JD 摘要（fetch 阶段已拿到）
    - 点击卡片后的右侧面板：公司介绍全文、福利明细（写在 JD 的「薪资福利」段）

    独立 jobdetail 页有腾讯云 EdgeOne 人机验证，所以走点击面板这条路。
    列表数据的 welfareLabel 恒为空，福利只能从面板的 JD 正文里解析。

    面板只对**当前列表里存在的卡片**生效，所以每个岗位都要先按岗位名
    重新搜一次，把它放进列表再点。
    """
    targets = [j for j in jobs[:limit] if j.title]
    if not targets:
        return

    page = get_page()
    jl = CITY_JL.get("青岛", "703")

    filled = intros = hr_filled = 0
    misses = 0
    for job in targets:
        # 用岗位名做搜索词，让目标卡片出现在列表里
        kw = job.title[:12]
        url = BASE.format(jl=jl, pno=1, kw=kw)
        if not safe_get(page, url, wait=4.5):
            misses += 1
            if misses >= 4:
                break
            continue

        cards = dom.finds(page, PLATFORM, "card", timeout=5)
        card = _find_card(cards, job)
        if card is None:
            misses += 1
            if misses >= 5:
                print("    智联无法定位卡片，停止补全", flush=True)
                break
            continue

        try:
            card.click()
        except Exception:
            misses += 1
            continue
        page.wait(2.5)

        panel = dom.find(page, PLATFORM, "detail_panel")
        text = (panel.text if panel else "") or ""
        if not text or len(text) < 100:
            misses += 1
            if misses >= 4:
                print("    智联详情面板连续未命中，停止补全", flush=True)
                break
            continue

        misses = 0

        # 公司信息区块：融资 · 规模 · 行业，一次拿全
        info = dom.find(page, PLATFORM, "company_header")
        if info:
            _read_company_header(info, job)

        # 公司介绍
        i = text.find("公司介绍")
        if i > 0:
            seg = re.sub(r"\s+", " ", text[i + 4:])[:500].strip()
            if len(seg) > 20:
                job.company_intro = seg
                intros += 1

        # JD 全文（面板比列表摘要更完整）
        j = text.find("职位描述")
        if j >= 0:
            end = i if i > j else j + 1800
            jd = re.sub(r"\s+", " ", text[j + 4:end]).strip()
            if len(jd) > len(job.jd_text or ""):
                job.jd_text = jd[:1500]
                filled += 1

        wf = _extract_welfare(text)
        if wf:
            job.welfare = wf

        blob = f"{job.welfare} {job.jd_text}"
        job.work_rest = job.work_rest or parse_work_rest(blob)
        job.work_hours = job.work_hours or parse_work_hours(blob)

        m = re.search(r"(工作地点|上班地址|工作地址)[：:]?\s*([^\n]{5,80})", text)
        if m:
            job.address = m.group(2).strip()[:120]

        # 「职位发布者」区块：HR 姓名、职位、在线状态、回复数据
        _read_publisher(page, job)
        if job.hr_active:
            hr_filled += 1

    print(f"    智联补 JD {filled} 条、公司介绍 {intros} 条、HR信息 {hr_filled} 条", flush=True)


def _read_company_header(block, job: Job) -> None:
    """解析公司信息头部。

    文本形如：
        松立控股集团股份有限公司
        B轮 · 300-499人 · 出行服务、软件/IT服务、房地产开发经营 已审核
        26个在招职位

    第二行用 `·` 分隔，依次是融资 / 规模 / 行业，比正则全文扫描精准得多，
    也不会把 JD 里提到的「B轮」误当成本公司的融资阶段。
    """
    lines = [x.strip() for x in (block.text or "").split("\n") if x.strip()]
    for line in lines:
        if "·" not in line:
            continue
        parts = [p.strip() for p in line.split("·") if p.strip()]
        for p in parts:
            if not job.company_financing and re.search(
                r"^(未融资|不需要融资|天使轮|A轮|B轮|C轮|D轮|E轮|已上市|战略融资|融资未公开|IPO上市)", p
            ):
                job.company_financing = re.match(r"^[^\d\s]+(?:轮)?(?:上市)?", p).group(0)[:10]
            elif not job.company_size and re.search(r"(\d+-\d+人|少于\d+人|\d+人以上)", p):
                job.company_size = re.search(r"(\d+-\d+人|少于\d+人|\d+人以上)", p).group(1)
            elif not job.company_industry and len(p) > 2:
                job.company_industry = re.sub(r"\s*已审核.*$", "", p)[:30]
        break


def _read_publisher(page, job: Job) -> None:
    """读「职位发布者」区，拿 HR 姓名/职位与活跃数据。

    该区块的结构（智联详情面板右上角）：
        .job-detail-publisher__name         HR 姓名，如「李女士」
        .job-detail-publisher__job-title    「人事 · 青岛某某有限公司」
        .job-detail-publisher__online       「在线」标记，不在线时不存在
        .job-detail-publisher__activity-tag 多个标签，如「今日回复50+次」「高回复率」

    活跃度拼成「在线·今日回复50+次·高回复率」，比单看在线状态信息量大。
    """
    name_el = dom.find(page, PLATFORM, "publisher_name")
    if name_el and not job.hr_name:
        name = (name_el.text or "").strip()
        if name:
            job.hr_name = name[:20]

    # 职位与公司：「人事经理 · 青岛天人环境股份有限公司」
    jt_el = dom.find(page, PLATFORM, "publisher_title")
    if jt_el:
        raw = (jt_el.text or "").strip()
        if "·" in raw:
            role, _, company = raw.partition("·")
            role = role.strip()
            company = company.strip()
            # HR 职位信息并入 hr_name，形如「李女士（人事经理）」
            if role and job.hr_name and role not in job.hr_name:
                short = re.sub(r"(经理|主管|专员|总监|HRD|HRBP|HR)$", "", role)[:6]
                if short and short not in job.hr_name:
                    job.hr_name = f"{job.hr_name}（{role[:12]}）"
            if company and (not job.company or job.company.startswith("（")):
                job.company = company[:40]

    parts: list[str] = []
    online = dom.find(page, PLATFORM, "publisher_online")
    if online and (online.text or "").strip():
        parts.append((online.text or "").strip())

    for tag in dom.finds(page, PLATFORM, "publisher_tags"):
        t = (tag.text or "").strip()
        if t and t not in parts:
            parts.append(t)

    if parts:
        job.hr_active = "·".join(parts)[:40]


# 福利关键词：覆盖「五险一金」「商业医疗保险」「年度体检」「带薪年假」等写法
_WELFARE_TOKENS = (
    "五险一金", "六险一金", "五险", "公积金", "商业医疗保险", "补充医疗",
    "年终奖", "年终奖金", "绩效奖金", "项目奖金", "股票期权", "期权", "股权",
    "带薪年假", "年假", "带薪病假", "法定假期", "节日福利", "节日礼品",
    "年度体检", "定期体检", "体检", "员工旅游", "团建", "团队活动",
    "餐补", "餐饮补贴", "交通补贴", "通讯补贴", "住房补贴", "高温补贴",
    "加班费", "加班补助", "调休", "弹性工作", "弹性工作制", "周末双休",
    "双休", "免费班车", "班车", "员工宿舍", "住宿", "培训", "技术培训",
    "零食", "下午茶", "免费工作餐", "员工关怀", "礼金", "慰问金",
)


def _extract_welfare(text: str) -> str:
    """从 JD 正文里提取福利项。

    智联的福利不单独成字段，而是写在 JD 的「薪资福利」段落里，
    形如「五险一金、商业医疗保险、绩效奖金、年度体检、带薪年假」。
    """
    found: list[str] = []
    for tok in _WELFARE_TOKENS:
        if tok in text:
            found.append(tok)
    # 去掉被更长词包含的短词（有「五险一金」就不要「五险」）
    out: list[str] = []
    for t in sorted(set(found), key=len, reverse=True):
        if not any(t != o and t in o for o in out):
            out.append(t)
    return "、".join(out[:10])


def _find_card(cards, job: Job):
    """在卡片列表里按「岗位名开头 + 公司名开头」定位目标卡片。"""
    want_t = (job.title or "")[:8]
    want_c = (job.company or "")[:6]
    for c in cards:
        t = (c.text or "").replace("\n", "")
        if want_t and want_t in t and (not want_c or want_c in t):
            return c
    return None
