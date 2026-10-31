# -*- coding: utf-8 -*-
"""求职岗位采集主流程。

用法：
    python -m scripts.main collect --keywords "Agent开发,前端开发" --city 青岛 \
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
        print(f"[{name}] 采集中……", flush=True)
        try:
            jobs = mod.fetch(profile, max_pages=pages)
            print(f"[{name}] 列表页拿到 {len(jobs)} 条", flush=True)
            if not jobs:
                reason = browser.is_blocked(browser.get_page())
                if reason:
                    blocked[name] = reason
                    print(f"[{name}] ⚠ 被拦截：{reason}", flush=True)
                    # 猎聘有 CLI 兜底：走独立接口通道，不受浏览器风控影响
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
                print(f"  - {k}: {v}  →  python -m scripts.main verify {k}")
    if errors:
        print("\n以下平台采集失败：")
        for k, v in errors.items():
            print(f"  - {k}: {v[:200]}")

    return all_jobs


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
    ap = argparse.ArgumentParser(prog="python -m scripts.main collect",
                                    description="多平台求职岗位采集与打分")
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
    print("=" * 60)

    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    raw = collect(profile, platforms, args.pages, args.enrich)
    if not raw:
        _diagnose_empty(platforms)
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


def _diagnose_empty(platforms: list[str]) -> None:
    """全空时给出可执行的下一步，而不是笼统的"检查网络"。

    区分两种情况很重要：新 profile 目录没登录态，和已有登录态但被风控。
    两者都表现为 0 条，但该做的事完全不同（登录 vs 过验证）。
    """
    profile = paths.profile_dir()
    # 判据用 cookies 文件：Chrome 首次启动就会建一堆目录（Crashpad/ShaderCache…），
    # 靠"目录是否为空"会误判。有任何站点写过 cookie，Cookies 文件才会非空。
    never_logged_in = _no_cookies(profile)

    reason = browser.is_blocked(browser.get_page())

    if never_logged_in:
        print("\n❌ 还没有登录过任何平台。")
        print(f"   登录态目录：{profile}（无招聘站点登录态）")
        need = [p for p in platforms if p in ("boss", "zhilian", "liepin")]
        if need:
            print("   按平台重要性依次登录：")
            for p in need:
                print(f"     python -m scripts.main login {p}")
        print("   51job 可不登录直接采集：--platforms 51job")
        return

    if reason:
        print(f"\n❌ 采集被风控拦截：{reason}")
        print(f"   过验证：python -m scripts.main verify {platforms[0] if platforms else '<平台>'}")
        print("   或等几分钟再试（风控通常自动解除）")
        return

    print("\n❌ 没有采集到任何岗位。按顺序排查：")
    print(f"   1) 登录态可能过期：python -m scripts.main login {platforms[0] if platforms else 'boss'}")
    print("   2) 单平台定位：--platforms zhilian 看是否能出数据")
    print("   3) 关键词过窄：试试 --keywords \"开发\" 这种宽词")
    print("   4) 端口冲突：export JOBHUNT_PORT=9335 后重试")


def _no_cookies(profile: str) -> bool:
    """判断是否从未登录过任何招聘站点。

    不能用「cookies 文件大小」判断——未登录访问也会写入大量会话 cookie。
    改为查 sqlite 里有没有目标站点的持久化 cookie（expires_utc > 0 表示有
    明确过期时间，通常是登录态；会话 cookie 过期时间为 0）。
    """
    cookie = os.path.join(profile, "Default", "Network", "Cookies")
    if not os.path.exists(cookie):
        return True

    import shutil
    import sqlite3
    import tempfile

    tmp = os.path.join(tempfile.gettempdir(), f"jh_cookies_{os.getpid()}.db")
    try:
        shutil.copy2(cookie, tmp)
        con = sqlite3.connect(tmp)
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM cookies WHERE (host_key LIKE '%zhipin%' "
                "OR host_key LIKE '%zhaopin%' OR host_key LIKE '%liepin%' "
                "OR host_key LIKE '%51job%') AND expires_utc > 0"
            ).fetchone()
            return not (row and row[0] > 0)
        finally:
            con.close()
    except Exception:
        return True
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
