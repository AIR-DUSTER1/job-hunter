# -*- coding: utf-8 -*-
"""求职岗位采集主流程。

用法：
    python -m jobhunt.main --keywords "Agent开发,前端开发" --city 青岛 \
        --salary 10 20 --years 2 --pages 2 --enrich 20

流程：采集 → 关键词过滤 → 外包识别 → 打分 → 排序 → 导出 Excel
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import traceback

from .adapters import boss, job51, liepin, liepin_cli, zhilian
from .core import browser, paths
from .core import merge
from .core.models import Job, export
from .core.scoring import Profile, judge_outsourcing, score_job

OUT_DIR = paths.out_dir()

ADAPTERS = {
    "51job": job51,
    "liepin": liepin,
    "zhilian": zhilian,
    "boss": boss,
}

# 每个目标方向展开成一组同义词，岗位名命中任一即可
DIRECTION_SYNONYMS = {
    "agent": ["agent", "智能体", "multi-agent", "multiagent", "langchain", "rag", "大模型应用", "llm应用"],
    "前端": ["前端", "frontend", "front-end", "vue", "react", "web前端", "h5", "小程序"],
    "ai应用": ["ai应用", "ai 应用", "ai开发", "大模型", "llm", "ai工程师", "aigc"],
}


def _expand(kw: str) -> list[str]:
    """把「Agent开发」这类关键词展开成一组可匹配的词。"""
    k = kw.lower().replace(" ", "")
    out = {k}
    # 去掉"开发/工程师/岗位"等通用后缀，保留核心词
    core = k
    for suffix in ("开发工程师", "开发", "工程师", "岗位", "工程师岗位"):
        if core.endswith(suffix) and len(core) > len(suffix):
            core = core[: -len(suffix)]
            break
    if len(core) >= 2:
        out.add(core)
    for key, syns in DIRECTION_SYNONYMS.items():
        if key in k or k in key or key in core:
            out.update(syns)
    return [x for x in out if len(x) >= 2]


def _is_relevant(job: Job, keywords: list[str]) -> bool:
    title = (job.title or "").lower()
    if not title:
        return False
    for kw in keywords:
        for token in _expand(kw):
            if token in title:
                return True
    return False


def collect(profile: Profile, platforms: list[str], pages: int, enrich_limit: int) -> list[Job]:
    all_jobs: list[Job] = []
    errors: dict[str, str] = {}
    blocked: dict[str, str] = {}
    fallback_used: list[str] = []

    # BOSS 风控最敏感，排到最后跑：前面平台的访问不会被它连累
    order = sorted(platforms, key=lambda p: 1 if p == "boss" else 0)

    for idx, name in enumerate(order):
        mod = ADAPTERS.get(name)
        if not mod:
            continue
        if idx > 0:
            browser.platform_cooldown(15)

        # 猎聘优先走 CLI：字段更全（含行业/规模/融资/福利标签）、不受风控影响、
        # 也不会触发短信验证。浏览器只在需要 JD 全文和 HR 活跃时才用。
        if name == "liepin" and liepin_cli.usable():
            print("[liepin] 使用猎聘 CLI 采集（字段更全，不触发风控）……", flush=True)
            try:
                jobs = liepin_cli.fetch(profile, max_pages=pages)
                print(f"[liepin] CLI 拿到 {len(jobs)} 条", flush=True)
                if jobs:
                    if enrich_limit > 0:
                        _enrich_liepin_hybrid(jobs, enrich_limit)
                    all_jobs.extend(jobs)
                    continue
            except Exception as e:
                print(f"[liepin] CLI 失败，改用浏览器：{str(e)[:100]}", flush=True)

        print(f"[{name}] 采集中……", flush=True)
        try:
            jobs = mod.fetch(profile, max_pages=pages)
            print(f"[{name}] 列表页拿到 {len(jobs)} 条", flush=True)
            if not jobs:
                reason = browser.is_blocked(browser.get_page())
                if reason:
                    blocked[name] = reason
                    print(f"[{name}] ⚠ 被拦截：{reason}", flush=True)
                    # 浏览器被拦时用 CLI 兜底（可用性已在上面判断过）
                    jobs = _try_cli_fallback(name, profile, pages)
                    if jobs:
                        fallback_used.append(name)
            if not jobs:
                continue
            if enrich_limit > 0:
                mod.enrich(jobs, limit=enrich_limit)
                filled = sum(1 for j in jobs[:enrich_limit] if j.jd_text or j.address)
                print(f"[{name}] 详情补全 {filled}/{min(len(jobs), enrich_limit)} 条", flush=True)
            all_jobs.extend(jobs)
        except Exception as e:
            errors[name] = str(e)
            print(f"[{name}] ⚠ 失败：{str(e)[:160]}", flush=True)

    if blocked:
        print("\n以下平台被风控拦截：")
        for k, v in blocked.items():
            if k in fallback_used:
                print(f"  - {k}: {v} → 已自动改用猎聘 CLI 取数")
            else:
                print(f"  - {k}: {v}  →  python jobhunt/verify.py {k}")
    if errors:
        print("\n以下平台采集失败：")
        for k, v in errors.items():
            print(f"  - {k}: {v[:200]}")

    return all_jobs


def _enrich_liepin_hybrid(jobs: list[Job], limit: int) -> None:
    """猎聘混合补全：CLI 已给出列表字段，只需浏览器补 JD 全文与 HR 活跃。

    CLI 提供的字段（岗位/公司/薪资/经验/学历/行业/规模/融资/福利标签）已经比
    浏览器列表页更全，所以这里只针对 CLI 缺的两项：
    - JD 全文（CLI 只有列表，没有详情接口）
    - HR 姓名与活跃状态（只在详情页顶部）

    浏览器一旦被风控就立刻停，避免把刚恢复的会话又打挂。
    """
    from .adapters import liepin as _lp

    need = [j for j in jobs if j.apply_url and not j.jd_text][:limit]
    if not need:
        return
    try:
        _lp.enrich(need, limit=len(need))
        got = sum(1 for j in need if j.jd_text)
        print(f"[liepin] 浏览器补 JD/HR {got}/{len(need)} 条", flush=True)
    except Exception as e:
        print(f"[liepin] 详情补全跳过：{str(e)[:100]}", flush=True)


def _try_cli_fallback(name: str, profile: Profile, pages: int) -> list[Job]:
    """浏览器被风控时，尝试用官方 CLI 兜底。"""
    if name != "liepin":
        return []

    if not liepin_cli.available():
        print(
            "\n💡 猎聘被风控，且未检测到猎聘 CLI。\n   "
            + liepin_cli.INSTALL_HINT.replace("\n", "\n   "),
            flush=True,
        )
        return []

    if not liepin_cli.auth_ok():
        print(
            "\n💡 猎聘 CLI 已安装但未授权，先执行：liepin-cli setup\n"
            "   授权后可自动绕过浏览器风控。",
            flush=True,
        )
        return []

    print("[liepin] 改用猎聘 CLI 兜底取数……", flush=True)
    try:
        jobs = liepin_cli.fetch(profile, max_pages=pages)
        liepin_cli.enrich(jobs)
        print(f"[liepin] CLI 拿到 {len(jobs)} 条（详情字段需浏览器，暂为空）", flush=True)
        return jobs
    except Exception as e:
        print(f"[liepin] CLI 兜底失败：{str(e)[:120]}", flush=True)
        return []


def _match_direction(title: str, keywords: list[str]) -> str:
    """判断岗位属于哪个目标方向。

    - 只命中一个方向 → 返回该方向
    - 同时命中多个方向 → 返回「跨方向」
    - 都不命中 → 返回空串
    """
    t = (title or "").lower()
    if not t:
        return ""
    hits: list[str] = []
    for kw in keywords:
        if any(token in t for token in _expand(kw)):
            hits.append(kw)
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        return "跨方向"
    return ""


def process(jobs: list[Job], profile: Profile) -> list[Job]:
    """过滤噪声 → 标注方向 → 外包识别 → 打分 → 排序。"""
    kept: list[Job] = []
    dropped = 0
    for job in jobs:
        if not _is_relevant(job, profile.keywords):
            dropped += 1
            continue
        # 城市兜底过滤（站点偶尔会串城市）
        if job.city and profile.city not in job.city:
            dropped += 1
            continue
        job.direction = _match_direction(job.title, profile.keywords) or "其他"
        job.is_outsourcing, job.outsourcing_reason = judge_outsourcing(
            job.company, job.company_industry, job.jd_text, job.title
        )
        job.score, job.score_detail = score_job(job, profile)
        job.fetched_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        kept.append(job)

    print(f"\n过滤掉 {dropped} 条不相关（关键词/城市），保留 {len(kept)} 条")
    by_dir: dict[str, int] = {}
    for j in kept:
        by_dir[j.direction] = by_dir.get(j.direction, 0) + 1
    if by_dir:
        print("按方向：" + "、".join(f"{k} {v} 条" for k, v in
                                     sorted(by_dir.items(), key=lambda kv: -kv[1])))
    kept.sort(key=lambda j: (-j.score, j.platform))
    return kept


def main() -> int:
    ap = argparse.ArgumentParser(description="多平台求职岗位采集与打分")
    ap.add_argument("--keywords", default="Agent开发,前端开发",
                    help="逗号分隔的目标岗位关键词")
    ap.add_argument("--city", default="青岛")
    ap.add_argument("--salary", nargs=2, type=float, default=[10, 20],
                    metavar=("MIN", "MAX"), help="期望月薪区间（K）")
    ap.add_argument("--years", type=float, default=2, help="工作年限")
    ap.add_argument("--pages", type=int, default=2, help="每个关键词翻页数")
    ap.add_argument("--enrich", type=int, default=20,
                    help="每个平台补详情的条数（0=不补，越快）")
    ap.add_argument("--platforms", default="51job,liepin,zhilian,boss")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    profile = Profile(
        keywords=[k.strip() for k in args.keywords.split(",") if k.strip()],
        city=args.city,
        salary_min=args.salary[0],
        salary_max=args.salary[1],
        years=args.years,
    )

    print("=" * 60)
    print(f"目标岗位：{'、'.join(profile.keywords)}")
    print(f"城市：{profile.city} | 期望薪资：{profile.salary_min}-{profile.salary_max}K "
          f"| 经验：{profile.years} 年")

    # 猎聘 CLI 是可选增强，采集前先报状态——用户才知道字段会不会更全
    if "liepin" in [p.strip() for p in args.platforms.split(",")]:
        ok, msg = liepin_cli.doctor()
        if ok:
            print(f"猎聘 CLI：已就绪（字段更全，不触发风控）")
        else:
            print("猎聘 CLI：不可用，将退回浏览器采集")
            print("  " + msg.replace("\n", "\n  "))
    print("=" * 60)

    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    raw = collect(profile, platforms, args.pages, args.enrich)
    if not raw:
        print("\n❌ 没有采集到任何岗位。请检查登录状态或网络。")
        return 1

    ranked = process(raw, profile)

    os.makedirs(OUT_DIR, exist_ok=True)
    latest_json = os.path.join(OUT_DIR, "最新岗位清单.json")
    existing = merge.load_existing(latest_json)
    # 本次实际采到的平台，用于判断旧记录该不该丢
    active_platform_names = {j.platform for j in ranked}
    if existing:
        ranked, added, updated, carried = merge.merge(
            ranked, existing, active_platforms=active_platform_names
        )
        ranked.sort(key=lambda j: (-j.score, j.platform))
        print(f"增量合并：新增 {added} 条，刷新 {updated} 条，"
              f"保留历史已投递 {carried} 条")

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M")
    out_path = args.out or os.path.join(OUT_DIR, f"岗位清单_{stamp}.xlsx")
    export(ranked, out_path)

    # 固定文件名的最新快照：下次运行以它为合并基准
    with open(latest_json, "w", encoding="utf-8") as f:
        json.dump([j.__dict__ for j in ranked], f, ensure_ascii=False, indent=1)
    # 同时留一份带时间戳的历史
    with open(out_path.replace(".xlsx", ".json"), "w", encoding="utf-8") as f:
        json.dump([j.__dict__ for j in ranked], f, ensure_ascii=False, indent=1)

    print("\n" + "=" * 60)
    print(f"✅ 共 {len(ranked)} 条岗位")
    print(f"   Excel: {out_path}")
    print(f"   最新快照: {latest_json}")
    print("\nTop 10：")
    print(f"{'得分':>5}  {'平台':<6} {'岗位':<22} {'公司':<20} {'薪资':<12} {'外包'}")
    for j in ranked[:10]:
        print(f"{j.score:>5.1f}  {j.platform:<6} {j.title[:20]:<22} "
              f"{j.company[:18]:<20} {j.salary[:10]:<12} {j.is_outsourcing}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
