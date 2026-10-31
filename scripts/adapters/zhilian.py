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
  var card = document.querySelector(".job-card");
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
    """读取页面 Vue 数据里的岗位列表。失败返回空列表。"""
    try:
        data = page.run_js(_READ_LIST_JS)
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
    for card in safe_eles(page, "css:.job-card"):
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
    """智联列表数据已含 JD 摘要、上架时间、链接，详情补全价值有限。

    独立的 jobdetail 页面有腾讯云 EdgeOne 人机验证，逐条打开很容易被拦；
    而列表页点击卡片打开右侧面板这条路，能拿到的最多是地址和公司简介。
    权衡下来：只补「列表里缺 JD」的岗位，且用列表重载的方式，代价最小。
    """
    targets = [j for j in jobs[:limit] if j.title and not j.jd_text]
    if not targets:
        print("    智联列表已含 JD，跳过详情补全", flush=True)
        return

    # 重新加载搜索页，从 Vue 数据里按标题+公司回填
    page = get_page()
    jl = CITY_JL.get("青岛", "703")
    filled = 0
    for kw in {j.title[:6] for j in targets}:
        url = BASE.format(jl=jl, pno=1, kw=kw)
        if not safe_get(page, url, wait=5):
            continue
        recs = _read_vue_list(page)
        if not recs:
            continue
        index = {}
        for rec in recs:
            key = ((rec.get("name") or "").strip(), (rec.get("company") or "").strip())
            index[key] = rec
        for job in targets:
            rec = index.get((job.title, job.company))
            if not rec or not (rec.get("jd") or "").strip():
                continue
            job.jd_text = re.sub(r"\s+", " ", rec["jd"])[:1500]
            if not job.published:
                job.published = (rec.get("publish") or "").strip()
            blob = f"{job.welfare} {job.jd_text}"
            job.work_rest = job.work_rest or parse_work_rest(blob)
            job.work_hours = job.work_hours or parse_work_hours(blob)
            filled += 1
        if filled >= len(targets):
            break

    print(f"    智联从列表数据回填 JD {filled}/{len(targets)} 条", flush=True)
