# -*- coding: utf-8 -*-
"""站点结构诊断：判断"采不到"是被风控、结构改了，还是真没岗位。

用法：
    python -m scripts.main diagnose                        # 四平台全检
    python -m scripts.main diagnose --platforms boss       # 只查一个
    python -m scripts.main diagnose --detail 2             # 每个平台补 2 条详情一起查
    python -m scripts.main diagnose --apply                # 固化本次验证过的候选
    python -m scripts.main diagnose --simulate boss        # 自测：假装选择器失效

为什么需要它：站点改版时 `fetch()` 返回空列表，和"这个关键词没岗位"、"被风控"
长得一模一样。以前只能靠人翻 references/platforms.md 猜，现在这里给出带证据的
结论（卡片数、各字段填充率、候选选择器、HTML 快照），并把能自动修的部分固化进
selectors.json。

`--apply` 只接受**本次在真实页面上验证过**的候选：推断出的选择器要用适配器自己的
`_parse_card` 跑一遍，title 填充率 ≥ 0.6 才算数。不做盲写。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse

from .adapters import boss, job51, liepin, zhilian
from .core import browser, dom, health, paths, selectors
from .core.scoring import Profile

# 各平台"打开列表页"所需的信息。适配器的 URL 构造逻辑本来就各不相同，
# 这里只做诊断用的一页抓取，不改变采集链路。
DIAG_SPEC = {
    "boss": {
        "module": boss,
        "city": lambda p: boss.CITY_CODE.get(p.city, "101120200"),
        "url": lambda p, kw, city: f"{boss.BASE}?query={urllib.parse.quote(kw)}&city={city}&page=1",
    },
    "zhilian": {
        "module": zhilian,
        "city": lambda p: zhilian.CITY_JL.get(p.city, "703"),
        "url": lambda p, kw, city: zhilian.BASE.format(jl=city, pno=1, kw=kw),
    },
    "job51": {
        "module": job51,
        "city": lambda p: job51.CITY_CODE.get(p.city, "120300"),
        "url": lambda p, kw, city: (
            f"{job51.BASE}?jobArea={city}&keyword={urllib.parse.quote(kw)}"
            f"&searchType=2&pageNum=1"
        ),
    },
    "liepin": {
        "module": liepin,
        "city": lambda p: liepin.CITY_DQ.get(p.city, "250070"),
        "url": lambda p, kw, city: f"{liepin.BASE}?key={urllib.parse.quote(kw)}&dq={city}&currentPage=0",
    },
}

# 平台 key 别名：collect/enrich 用的是 "51job"，这里的模块 key 是 "job51"。
# 用户在命令行两种写法都可能用，统一归一，避免 `--platforms 51job` 被静默跳过。
ALIASES = {
    "51job": "job51",
    "job51": "job51",
    "boss": "boss",
    "zhilian": "zhilian",
    "liepin": "liepin",
}


def _canon(name: str) -> str:
    return ALIASES.get((name or "").strip().lower(), (name or "").strip().lower())


# 需要改代码、无法靠选择器配置修复的情形，报告里点名到函数
_CODE_FIXES = {
    "zhilian": "智联的链接/发布时间来自 Vue 数据读取器 scripts/adapters/zhilian.py::_READ_LIST_JS"
               "（如果 .job-card 宿主元素也变了，该 JS 里的选择器要一起改）",
    "boss": "BOSS 的薪资解码在 scripts/adapters/boss.py::decode_obfuscated，"
            "若薪资仍为空但卡片有文本，检查私有区字符映射是否变了",
}

VERDICT_LABEL = {
    health.OK: "OK  ",
    health.DEGRADED: "降级",
    health.BROKEN: "结构变更",
    health.BLOCKED: "被风控",
}


def _parser_for(platform: str):
    """取该平台的单卡片解析函数。

    必须惰性取，不能写成 {"boss": boss._parse_card, ...}[platform]——字典字面量
    会先把所有值都求值一遍，任何一个模块缺这个函数就整体 AttributeError。
    """
    module = {
        "boss": boss,
        "zhilian": zhilian,
        "job51": job51,
        "liepin": liepin,
    }[platform]
    fn = getattr(module, "_parse_card", None)
    if fn is None:
        raise AttributeError(f"{module.__name__} 缺少 _parse_card(card) 入口")
    return fn


def _parse_cards_via(platform: str, page, module) -> tuple[list, int]:
    """按平台各自的方式取出并解析卡片，返回 (jobs, 卡片 DOM 数)。

    解析必须紧跟在取元素之后。BOSS 用的是虚拟列表，卡片会周期性重渲染，
    旧元素引用会失效（实测：取到卡片后等 2 秒再读 .job-salary，15 个全变空）。
    这里不做任何等待，一次遍历里把元素读完、存成 Job 值。
    """
    if platform == "zhilian":
        # 智联优先读 Vue 数据，卡片 DOM 只是兜底，所以两者都看
        cards = dom.finds(page, platform, "card", timeout=4)
        recs = zhilian._read_vue_list(page)
        if recs:
            jobs = [j for j in (zhilian._job_from_dict(r) for r in recs) if j]
            return jobs, len(cards)
        return [j for j in (zhilian._parse_card(c) for c in cards) if j], len(cards)

    cards = dom.finds(page, platform, "card", timeout=5)
    parse = _parser_for(platform)
    jobs = []
    for c in cards:
        try:
            j = parse(c)
        except Exception:
            j = None
        if j:
            jobs.append(j)

    # 虚拟列表重渲染会让首轮部分卡片读到空字段。用同一批元素再补一轮，
    # 只填空值、不覆盖已有值——不重新导航，避免额外请求。
    if platform == "boss" and jobs and sum(1 for j in jobs if j.salary) < len(jobs) * 0.6:
        for c, j in zip(cards, jobs):
            if j.salary:
                continue
            try:
                s = dom.find(c, platform, "card_salary", timeout=1)
                if s and (s.text or "").strip():
                    j.salary = module.decode_obfuscated((s.text or "").strip())
            except Exception:
                continue
        if not any(j.salary for j in jobs):
            # 整批都没读到：重新取一次卡片元素再解析一遍
            fresh = dom.finds(page, platform, "card", timeout=5)
            rescued = []
            for c in fresh:
                try:
                    j = parse(c)
                except Exception:
                    j = None
                if j:
                    rescued.append(j)
            if rescued:
                jobs = rescued

    return jobs, len(cards)


# 有些平台的卡片 DOM 本身不含详情链接，链接只在接口/Vue 数据里。
# 智联就是：新版 .job-card 列表的标题是 span，点击走 SPA 跳转，DOM 里没有 <a href>。
# 对这类平台"链接率"不能当验证信号，否则永远判候选不可用。
_LINKLESS_CARDS = {"zhilian"}


def _try_candidates(platform: str, page, module) -> list[dict]:
    """推断候选选择器，并用适配器自己的解析函数在真实页面上打分。"""
    infer = dom.infer_card_candidates(page, limit=6)
    verified: list[dict] = []

    parse = _parser_for(platform)
    need_link = platform not in _LINKLESS_CARDS

    for cand in infer:
        sel = cand["selector"]
        try:
            els = browser.safe_eles(page, sel, timeout=3)
        except Exception:
            continue
        if not els:
            continue
        parsed = []
        for el in els[:20]:
            try:
                j = parse(el)
            except Exception:
                j = None
            if j:
                parsed.append(j)
        if not parsed:
            continue

        # 只靠岗位名不够：几个平台的 _parse_card 都是"第一行当标题"，任何多行
        # 文本块（筛选下拉、页脚）都能"解析成功"。真正的职位卡片还必须带详情链接。
        n = len(parsed)
        title_rate = sum(1 for j in parsed if (j.title or "").strip()) / n
        link_rate = sum(1 for j in parsed if (j.apply_url or "").strip()) / n
        salary_rate = sum(1 for j in parsed if (j.salary or "").strip()) / n

        cand = dict(cand)
        cand["parsed"] = n
        cand["title_rate"] = round(title_rate, 3)
        cand["link_rate"] = round(link_rate, 3)
        cand["salary_rate"] = round(salary_rate, 3)
        # 阈值：卡片数 ≥3、岗位名 ≥60%。详情链接 ≥60% 只在需要它的平台检查
        # （见 _LINKLESS_CARDS）——链接率是区分"职位卡片"与"其他文本块"最可靠的
        # 信号，但智联的卡片 DOM 本身就没有链接。
        cand["verified"] = (
            len(els) >= 3
            and title_rate >= 0.6
            and (link_rate >= 0.6 if need_link else True)
        )
        verified.append(cand)

    verified.sort(key=lambda c: (not c["verified"], -c["title_rate"], -c["link_rate"], -c["parsed"]))
    return verified


def _blocks(page) -> str:
    """风控判断：URL 特征 + 页面文案双保险。

    注意别在这里等：BOSS 是虚拟列表，任何额外等待都可能让卡片重渲染，
    导致随后的解析读不到薪资。body 文本用极短超时，取不到就跳过文案判断。
    """
    reason = browser.is_blocked(page)
    if reason:
        return reason
    try:
        head = (page.ele("tag:body", timeout=0).text or "")[:800]
    except Exception:
        head = ""
    for marker in ("安全验证", "人机验证", "请完成验证", "正在验证连接安全性", "访问过于频繁"):
        if marker in head:
            return marker
    return ""


def diagnose_platform(platform: str, profile: Profile, *, detail: int = 1,
                      simulate: bool = False) -> health.PlatformReport:
    """诊断单个平台。simulate=True 时故意打断卡片选择器，验证自愈链路。"""
    spec = DIAG_SPEC[platform]
    module = spec["module"]
    kw = profile.keywords[0] if profile.keywords else "开发"
    city = spec["city"](profile)
    url = spec["url"](profile, kw, city)

    page = browser.get_page()
    if not browser.safe_get(page, url, wait=4.5):
        rep = health.PlatformReport(platform=platform, verdict=health.BROKEN)
        rep.notes.append(f"页面打不开：{url}")
        return rep

    blocked = _blocks(page)
    if blocked:
        rep = health.PlatformReport(platform=platform, verdict=health.BLOCKED)
        rep.notes.append(f"被风控拦截（{blocked}），先跑 python -m scripts.main verify {platform}")
        rep.snapshots = dom.snapshot(page, f"{platform}_blocked", paths.diagnose_dir())
        return rep

    dom.reset_stats()

    jobs, card_count = _parse_cards_via(platform, page, module)

    if simulate:
        # 模拟改版：把解析结果废掉，强制走"容器失效"这条路径
        jobs, card_count = [], 0

    rep = health.check_parsed(platform, jobs, card_count)
    rep.fallbacks = dom.fallbacks()

    if rep.verdict in (health.BROKEN, health.DEGRADED):
        rep.snapshots = dom.snapshot(page, platform, paths.diagnose_dir())
        inferred = _try_candidates(platform, page, module)
        rep.candidates = inferred
        usable = [c for c in inferred if c.get("verified")]
        if usable:
            rep.notes.append(
                f"推断出 {len(usable)} 个可用候选，可执行 "
                f"python -m scripts.main diagnose --platforms {platform} --apply 固化"
            )
        elif inferred:
            # 区分"解析不出岗位名"和"解析得出但没有详情链接"，两者的处置不同
            no_link = all(c.get("link_rate", 0) < 0.6 for c in inferred)
            if no_link:
                rep.notes.append(
                    "候选块能解析出文本但没有岗位详情链接，不是职位卡片；"
                    "多半是页面整体重写，需要人工看快照"
                )
            else:
                rep.notes.append("推断出候选但岗位名填充率不够，需要人工看快照")
        else:
            rep.notes.append("未推断出候选，页面结构可能整体重写，需要人工介入")
        if platform in _CODE_FIXES:
            rep.notes.append(_CODE_FIXES[platform])

    # 详情层：默认只补 1 条，控制风控风险
    if detail > 0 and jobs:
        sample = jobs[:detail]
        try:
            module.enrich(sample, limit=len(sample))
        except Exception as e:
            rep.notes.append(f"详情补全抛异常：{str(e)[:100]}")
        drep = health.check_detail_page(platform, sample)
        rep.fields.extend(drep.fields)
        rep.notes.extend(drep.notes)
        if drep.verdict != health.OK:
            rep.verdict = health._worse(rep.verdict, drep.verdict)
            if not rep.snapshots:
                rep.snapshots = dom.snapshot(page, f"{platform}_detail", paths.diagnose_dir())
            rep.candidates.extend(dom.infer_detail_candidates(page, limit=4))

    if dom.fallbacks():
        rep.notes.append("有字段靠后备选择器命中，建议尽快固化首选值")

    return rep


def _print_report(reports: list[health.PlatformReport]) -> None:
    print()
    print("=" * 66)
    for r in reports:
        label = VERDICT_LABEL.get(r.verdict, r.verdict)
        print(f"[{r.platform}] {label}")
        print(f"    {r.summary()}")
        for n in r.notes:
            print(f"    · {n}")
        for c in r.candidates[:3]:
            mark = "✓" if c.get("verified") else "×"
            print(f"    {mark} 候选 {c['selector']}  解析 {c.get('parsed', 0)} 条 "
                  f"岗位名 {c.get('title_rate', 0):.0%} "
                  f"链接 {c.get('link_rate', 0):.0%}")
        if r.snapshots:
            for k, v in r.snapshots.items():
                print(f"    快照 {k}: {v}")
    print("=" * 66)


def _next_steps(reports: list[health.PlatformReport], applied: list[str]) -> None:
    """报告末尾的"下一步"块：区分能自动修的、要改代码的、要人工过验证的。"""
    broken = [r for r in reports if r.verdict in (health.BROKEN, health.DEGRADED)]
    blocked = [r for r in reports if r.verdict == health.BLOCKED]

    print("\n下一步：")
    if not broken and not blocked:
        print("  ✅ 四个平台结构正常，无需处理")
        return

    for r in blocked:
        print(f"  {r.platform}: 先过验证 → python -m scripts.main verify {r.platform}")

    auto = [r for r in broken if any(c.get("verified") for c in r.candidates)]
    for r in auto:
        if r.platform in applied:
            print(f"  {r.platform}: 已固化候选选择器，建议复跑 collect 确认")
        else:
            print(f"  {r.platform}: 可自动修复 → "
                  f"python -m scripts.main diagnose --platforms {r.platform} --apply")

    manual = [r for r in broken if not any(c.get("verified") for c in r.candidates)]
    for r in manual:
        print(f"  {r.platform}: 需人工看快照后改 scripts/adapters/{r.platform}.py"
              f"（或 selectors.json）")

    if broken:
        print("\n  修复后复跑：python -m scripts.main collect --platforms <平台>")
        print("  验证通过后把结论补进 references/platforms.md")


def apply_candidates(reports: list[health.PlatformReport]) -> list[str]:
    """把本次验证过的候选固化进注册表。只接受 verified=True 的。"""
    applied = []
    for r in reports:
        ok = [c for c in r.candidates if c.get("verified")]
        if not ok:
            continue
        best = ok[0]
        path, old = selectors.apply(r.platform, "card", best["selector"], evidence=best)
        print(f"\n[{r.platform}] 已固化卡片选择器")
        print(f"    旧: {old[0] if old else '(无)'}")
        print(f"    新: {best['selector']}   解析 {best['parsed']} 条 "
              f"岗位名 {best.get('title_rate', 0):.0%} 链接 {best.get('link_rate', 0):.0%}")
        print(f"    写入: {path}  (备份 {os.path.basename(path)}.bak)")
        applied.append(r.platform)
    return applied


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.main diagnose",
                                 description="诊断站点结构是否变更")
    ap.add_argument("--platforms", default="51job,zhilian,liepin,boss")
    ap.add_argument("--city", default="青岛")
    ap.add_argument("--keywords", default="开发")
    ap.add_argument("--detail", type=int, default=1, help="每平台补几条详情一起查（0=跳过详情层）")
    ap.add_argument("--apply", action="store_true", help="固化本次验证通过的候选选择器")
    ap.add_argument("--simulate", default="", help="自测：指定平台，强制打断选择器")
    ap.add_argument("--json", action="store_true", help="只输出 JSON 报告")
    args = ap.parse_args()

    sim = _canon(args.simulate or "")
    if sim and sim not in DIAG_SPEC:
        print(f"未知平台 {args.simulate}，可选：{', '.join(sorted(DIAG_SPEC))}（也可写 51job）")
        return 2
    if sim and args.apply:
        print("--simulate 与 --apply 互斥：模拟结果不能固化进注册表")
        return 2

    profile = Profile(
        keywords=[k.strip() for k in args.keywords.split(",") if k.strip()],
        city=args.city, salary_min=10, salary_max=20, years=2,
    )

    if sim:
        platforms = [sim]
    else:
        platforms = [_canon(p) for p in args.platforms.split(",") if p.strip()]

    order = sorted(platforms, key=lambda p: 1 if p == "boss" else 0)
    reports: list[health.PlatformReport] = []
    for idx, name in enumerate(order):
        if name not in DIAG_SPEC:
            print(f"[{name}] 未知平台，跳过")
            continue
        if idx > 0:
            browser.platform_cooldown(15)
        if not args.json:
            print(f"[{name}] 诊断中……", flush=True)
        try:
            rep = diagnose_platform(name, profile, detail=args.detail, simulate=(name == sim))
        except Exception as e:
            rep = health.PlatformReport(platform=name, verdict=health.BROKEN)
            rep.notes.append(f"诊断抛异常：{str(e)[:160]}")
        reports.append(rep)

    applied: list[str] = []
    if args.apply:
        applied = apply_candidates(reports)

    out_dir = paths.diagnose_dir()
    import datetime as dt
    report_path = os.path.join(
        out_dir, f"结构变更_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    payload = health.merge(reports)
    payload["applied"] = applied
    payload["simulated"] = sim or None
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_report(reports)
        _next_steps(reports, applied)
        print(f"\n报告：{report_path}")

    return 0 if payload["overall"] == health.OK else 1


if __name__ == "__main__":
    sys.exit(main())
