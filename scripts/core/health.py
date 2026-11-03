# -*- coding: utf-8 -*-
"""采集结构健康检查。

区分三种"采集不到东西"的情况，这是当前最缺的能力——以前只有 URL 级的
`browser.is_blocked()`，选择器失效和真没岗位都表现为"0 条"，无法定位。

- **被风控**：URL 或页面特征命中验证页 → `BLOCKED`
- **结构变了**：页面能打开、DOM 正常，但卡片容器或关键字段的选择器命中率归零
- **真的没岗位**：卡片数正常，只是这个关键词没结果

字段填充率是核心指标：把适配器现成的 `_parse_card` 跑在真实卡片上，统计
每个字段的命中比例。改版通常先坏卡片内某几个字段，而不是整个卡片容器。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import selectors

# 结论三档
OK = "OK"
DEGRADED = "DEGRADED"
BROKEN = "BROKEN"
BLOCKED = "BLOCKED"


class StructureChanged(RuntimeError):
    """站点 DOM 结构与注册表不符，采集无法进行。

    继承 RuntimeError 是为了让 `collect.py` 里原有的 `except Exception` 分支
    继续生效，同时能单独捕获做自愈重试。
    """

    def __init__(self, platform: str, reason: str, *, report=None):
        self.platform = platform
        self.reason = reason
        self.report = report
        super().__init__(f"{platform} 页面结构变更：{reason}")


# 关键字段：缺了基本没法用，阈值严
CRITICAL_FIELDS = ("title", "apply_url")
# 次要字段：阈值放宽
SECONDARY_FIELDS = ("salary", "company", "experience", "education",
                    "jd", "address", "company_intro", "work_rest", "hr_active",
                    "published", "company_size", "welfare")


@dataclass
class FieldStats:
    name: str
    hit: int = 0
    total: int = 0

    @property
    def rate(self) -> float:
        return (self.hit / self.total) if self.total else 0.0

    def line(self) -> str:
        return f"{self.name} {self.hit}/{self.total}"


@dataclass
class PlatformReport:
    platform: str
    verdict: str = OK
    card_count: int = 0
    fields: list[FieldStats] = field(default_factory=list)
    fallbacks: list[dict] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    snapshots: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def add(self, name: str, hit: int, total: int) -> None:
        self.fields.append(FieldStats(name, hit, total))

    def rate(self, name: str) -> float:
        for f in self.fields:
            if f.name == name:
                return f.rate
        return 0.0

    def summary(self) -> str:
        parts = [f"{f.name} {f.hit}/{f.total}" for f in self.fields if f.total]
        head = f"{self.card_count} 张卡片"
        return head + (" | " + "  ".join(parts) if parts else "")

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "verdict": self.verdict,
            "card_count": self.card_count,
            "fields": [
                {"name": f.name, "hit": f.hit, "total": f.total,
                 "rate": round(f.rate, 3)}
                for f in self.fields
            ],
            "fallbacks": self.fallbacks,
            "candidates": self.candidates,
            "snapshots": self.snapshots,
            "notes": self.notes,
        }


def _rate_verdict(rate: float, critical: bool) -> str:
    """单字段命中率 → 健康档。"""
    if critical:
        if rate < 0.6:
            return BROKEN
        if rate < 0.9:
            return DEGRADED
        return OK
    if rate < 0.4:
        return BROKEN
    if rate < 0.7:
        return DEGRADED
    return OK


_RANK = {OK: 0, DEGRADED: 1, BROKEN: 2, BLOCKED: 3}


def _worse(a: str, b: str) -> str:
    return a if _RANK.get(a, 0) >= _RANK.get(b, 0) else b


def check_list_page(platform: str, cards: list, parse_fn) -> PlatformReport:
    """检查列表页：卡片数 + 卡片内各字段填充率。

    parse_fn 就是各适配器现成的 `_parse_card`，直接复用，不重写解析逻辑。
    """
    if not cards:
        rep = PlatformReport(platform=platform, card_count=0)
        rep.verdict = BROKEN
        rep.notes.append(f"卡片容器「{selectors.primary(platform, 'card')}」命中 0 个元素")
        return rep

    parsed = []
    for c in cards:
        try:
            job = parse_fn(c)
        except Exception:
            job = None
        if job is not None:
            parsed.append(job)

    return check_parsed(platform, parsed, card_count=len(cards))


def check_parsed(platform: str, parsed: list, card_count: int) -> PlatformReport:
    """已解析成 Job 的列表检查。

    智联这类"先读 Vue 数据、失败才解析卡片文本"的适配器，Jobs 可能全部来自
    Vue，而卡片 DOM 选择器已经失效。所以 card_count 和 parsed 要分开看：

    - parsed 有数据但 card_count == 0：**不能算 OK**。数据是接口路径给的，
      卡片选择器坏了只是暂时被掩盖；依赖 DOM 的字段（点击补详情、login/verify
      的登录判定）已经失效。
    - 两者都 0：容器失效。
    """
    rep = PlatformReport(platform=platform, card_count=card_count)

    if not card_count and not parsed:
        rep.verdict = BROKEN
        rep.notes.append(f"卡片容器「{selectors.primary(platform, 'card')}」命中 0 个元素")
        return rep
    if not parsed:
        rep.verdict = BROKEN
        rep.notes.append("卡片存在但一条都没解析出来，卡片内部字段选择器可能已失效")
        return rep

    rep.add("parsed", len(parsed), max(card_count, len(parsed)))
    n = max(len(parsed), 1)

    title = sum(1 for j in parsed if (j.title or "").strip())
    link = sum(1 for j in parsed if (j.apply_url or "").strip())
    salary = sum(1 for j in parsed if (j.salary or "").strip())
    company = sum(1 for j in parsed if (j.company or "").strip())
    exp = sum(1 for j in parsed if (j.experience or "").strip())
    edu = sum(1 for j in parsed if (j.education or "").strip())

    rep.add("title", title, n)
    rep.add("apply_url", link, n)
    rep.add("salary", salary, n)
    rep.add("company", company, n)
    rep.add("experience", exp, n)
    rep.add("education", edu, n)

    verdict = OK
    for name in ("title", "apply_url"):
        verdict = _worse(verdict, _rate_verdict(rep.rate(name), critical=True))
    for name in ("salary", "company"):
        verdict = _worse(verdict, _rate_verdict(rep.rate(name), critical=False))

    # 卡片 DOM 为 0 但数据照常产出的情况：数据来自接口/Vue，卡片选择器已失效。
    # 不能报 OK——依赖 DOM 的路径（点击补详情、login/verify 的登录判定）已经坏了。
    if not card_count:
        verdict = _worse(verdict, DEGRADED)
        rep.notes.append(
            f"卡片容器「{selectors.primary(platform, 'card')}」命中 0 个元素，"
            f"但拿到了 {len(parsed)} 条数据（来自接口/Vue）。"
            f"数据暂时正常，但点击补详情与登录判定已失效"
        )

    # 抓到的东西总量太少也是信号
    total_seen = max(card_count, len(parsed))
    if total_seen < 3:
        verdict = _worse(verdict, DEGRADED)
        rep.notes.append(f"只拿到 {total_seen} 条，容器选择器可能只匹配到部分元素")

    rep.verdict = verdict

    if verdict != OK:
        for f in rep.fields:
            if f.total and f.rate == 0:
                rep.notes.append(f"字段「{f.name}」全部为空")

    return rep


def check_detail_page(platform: str, jobs: list, unsupported: set[str] | None = None) -> PlatformReport:
    """检查详情页：JD / 地址 / 公司信息等填充率。

    平台产品层面不提供的字段（`unsupported_fields`）不参与判定，
    否则智联的 hr_active 会每次都报故障。
    """
    skip = unsupported if unsupported is not None else selectors.unsupported_fields(platform)
    rep = PlatformReport(platform=f"{platform}:detail", card_count=len(jobs))
    if not jobs:
        rep.verdict = DEGRADED
        rep.notes.append("没有可检查的详情样本")
        return rep

    n = len(jobs)
    checks = {
        "jd": lambda j: bool((j.jd_text or "").strip()),
        "address": lambda j: bool((j.address or "").strip()),
        "company_intro": lambda j: bool((j.company_intro or "").strip()),
        "hr_active": lambda j: bool((j.hr_active or "").strip()),
        "work_rest": lambda j: bool((j.work_rest or "").strip()),
        "company_size": lambda j: bool((j.company_size or "").strip()),
        "published": lambda j: bool((j.published or "").strip()),
    }

    verdict = OK
    for name, fn in checks.items():
        if name in skip:
            rep.notes.append(f"字段「{name}」平台不提供，跳过")
            continue
        hit = sum(1 for j in jobs if fn(j))
        rep.add(name, hit, n)
        # 只有 JD 是硬要求：拿不到 JD 说明详情容器大概率变了
        if name == "jd":
            verdict = _worse(verdict, _rate_verdict(hit / n, critical=True))

    rep.verdict = verdict
    if n and rep.rate("jd") == 0:
        rep.notes.append("JD 全部为空，详情页容器可能已变更")
    return rep


def merge(reports: list[PlatformReport]) -> dict:
    """多平台报告 → 可落盘的汇总结构。"""
    import datetime as dt
    overall = OK
    for r in reports:
        overall = _worse(overall, r.verdict if r.verdict != BLOCKED else BROKEN)
    return {
        "at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "overall": overall,
        "platforms": [r.to_dict() for r in reports],
    }
