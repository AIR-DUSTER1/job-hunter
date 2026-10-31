# -*- coding: utf-8 -*-
"""猎聘 CLI 兜底：浏览器被风控时改用 liepin-cli 取数据。

liepin-cli 走的是猎聘自己的接口通道，和浏览器会话的风控是分开的，所以
浏览器跳 safe.liepin.com 短信验证时，CLI 往往还能正常搜索。

它只提供 search（列表），没有详情接口——JD / 地址 / HR 活跃仍需要浏览器，
因此 CLI 兜底拿到的是「列表字段完整、详情字段为空」的结果。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from shutil import which

from ..core.models import Job
from ..core.scoring import parse_work_hours, parse_work_rest

# 青岛在 liepin-cli 里按城市名传
CLI_TIMEOUT = 40
INSTALL_HINT = (
    "猎聘 CLI 未就绪。它是可选增强：装上后猎聘字段更全且不触发短信验证。\n"
    "  安装：pip install liepin-cli\n"
    "  授权：liepin-cli setup      （扫码登录）\n"
    "  检查：liepin-cli auth status\n"
    "装好后重跑采集会自动启用，无需改代码。"
)


def _candidate_paths() -> list[str]:
    """所有可能的 liepin-cli 可执行文件位置。

    覆盖三种安装形态：
    1. PATH 上的（pip install 到全局环境）
    2. ~/bin 下的包装脚本（Windows 常见 .cmd）
    3. 独立 venv 里的（本案的安装方式）
    """
    import os

    out: list[str] = []
    # PATH：Windows 上 Python 的 subprocess 不会自动补扩展名，必须逐个试
    for name in ("liepin-cli.exe", "liepin-cli.cmd", "liepin-cli.bat", "liepin-cli"):
        p = which(name)
        if p and p not in out:
            out.append(p)

    home = os.path.expanduser("~")
    for rel in (
        ("bin", "liepin-cli.cmd"),
        ("bin", "liepin-cli.exe"),
        ("bin", "liepin-cli"),
        (".zcode", "workspace", "default", "liepin-cil", ".venv", "Scripts", "liepin-cli.exe"),
        (".zcode", "workspace", "default", "liepin-cli", ".venv", "Scripts", "liepin-cli.exe"),
        (".local", "bin", "liepin-cli"),
    ):
        c = os.path.join(home, *rel)
        if os.path.isfile(c) and c not in out:
            out.append(c)

    # 当前 Python 环境的 Scripts / bin（pip install 到本环境时）
    import sys
    for sub in ("Scripts", "bin"):
        c = os.path.join(sys.prefix, sub, "liepin-cli.exe")
        if os.path.isfile(c) and c not in out:
            out.append(c)
        c = os.path.join(sys.prefix, sub, "liepin-cli")
        if os.path.isfile(c) and c not in out:
            out.append(c)
    return out


# None=未探测过；""=探测过但没有
_EXE: str | None = None


def _exe() -> str | None:
    """返回第一个可执行的 liepin-cli 路径。"""
    global _EXE
    if _EXE is not None:
        return _EXE or None
    for c in _candidate_paths():
        if _runs(c):
            _EXE = c
            return c
    _EXE = ""
    return None


def _runs(exe: str) -> bool:
    """真的跑一下 --help，确认能执行（文件存在但缺依赖会失败）。"""
    try:
        r = subprocess.run([exe, "--help"], capture_output=True,
                           timeout=25, text=True, encoding="utf-8", errors="replace")
        return r.returncode == 0
    except Exception:
        return False


def available() -> bool:
    """liepin-cli 是否可用（已安装且能执行）。"""
    return _exe() is not None


def auth_ok() -> bool:
    """token 是否有效。"""
    exe = _exe()
    if not exe:
        return False
    try:
        r = subprocess.run([exe, "auth", "status"], capture_output=True,
                           timeout=25, text=True, encoding="utf-8", errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode == 0 and "token" in out.lower() and "****" in out
    except Exception:
        return False


def _run_search(keyword: str, city: str, page: int) -> list[dict]:
    exe = _exe()
    if not exe:
        return []
    cmd = [
        exe, "job", "search",
        "--job-name", keyword,
        "--address", city,
        "--page", str(page),
        "--output", "json",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=CLI_TIMEOUT, text=True,
                           encoding="utf-8", errors="replace")
    except (subprocess.TimeoutExpired, OSError):
        return []
    if r.returncode != 0:
        return []
    # 输出可能夹带横幅文字，取第一个完整 JSON 对象
    out = (r.stdout or "").strip()
    start = out.find("{")
    if start < 0:
        return []
    try:
        data = json.loads(out[start:])
    except json.JSONDecodeError:
        # 尝试按花括号配平截取
        depth = 0
        for i, ch in enumerate(out[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(out[start:i + 1])
                    except Exception:
                        return []
                    break
        else:
            return []
    return (data.get("data") or {}).get("list") or []


def _to_job(rec: dict) -> Job | None:
    title = (rec.get("jobName") or "").strip()
    if not title:
        return None
    job = Job(platform="猎聘")
    job.title = title
    company = (rec.get("company") or "").strip()
    # 猎聘常以「某知名公司」这类占位名隐藏雇主；CLI 偶有字段缺失
    job.company = company or "（猎聘未披露）"
    job.salary = (rec.get("salary") or "").strip()

    loc = (rec.get("location") or "").strip()
    job.city = loc if ("青岛" in loc or "·" in loc) else f"青岛-{loc}" if loc else ""

    job.education = (rec.get("education") or "").strip()
    job.experience = (rec.get("workYears") or "").strip()
    job.company_industry = (rec.get("industry") or "").replace(",", "、")
    job.company_financing = (rec.get("financingStage") or "").strip()
    job.company_size = (rec.get("companySize") or "").strip()

    url = (rec.get("jobDetailUrl") or "").strip()
    if url:
        job.apply_url = url.split("?")[0]

    tags = rec.get("companyTags") or []
    if tags:
        job.welfare = "、".join(str(t) for t in tags)

    job.remark = "数据来源：猎聘CLI（浏览器被风控）"
    return job


def fetch(profile, max_pages: int = 2) -> list[Job]:
    """用 liepin-cli 采集猎聘岗位。"""
    seen: set[str] = set()
    out: list[Job] = []
    for kw in profile.keywords:
        for page in range(max_pages):
            recs = _run_search(kw, profile.city, page)
            if not recs:
                break
            new = 0
            for rec in recs:
                job = _to_job(rec)
                if not job:
                    continue
                key = job.title + "|" + job.company
                if key in seen:
                    continue
                seen.add(key)
                out.append(job)
                new += 1
            if new < 3:
                break
    return out


def enrich(jobs: list[Job], limit: int = 0) -> None:
    """CLI 无详情接口，此处只做字段解析兜底（从已有文本里抽单双休/工时）。"""
    for job in jobs[:limit] or jobs:
        blob = f"{job.welfare} {job.title}"
        job.work_rest = job.work_rest or parse_work_rest(blob)
        job.work_hours = job.work_hours or parse_work_hours(blob)


def usable() -> bool:
    """CLI 是否可用（已安装且已授权）。

    与 available() 的区别：这个要求授权有效，因为未授权调 search 会直接失败。
    """
    return available() and auth_ok()


def doctor() -> tuple[bool, str]:
    """自检：返回 (是否就绪, 诊断说明)。

    分四步定位问题所在，避免用户看到笼统的"不可用"：
    1. 找不到可执行文件 → 没装
    2. 找到但跑不起来 → 安装损坏或缺依赖
    3. 跑得起来但未授权 → 需要 setup
    4. 都正常 → 就绪
    """
    paths = _candidate_paths()
    if not paths:
        return False, (
            "未找到 liepin-cli。\n"
            f"  已检查：PATH、~/bin、~/.zcode/workspace/default/*/.venv\n"
            f"  {INSTALL_HINT}"
        )

    exe = _exe()
    if not exe:
        return False, (
            f"找到 {len(paths)} 个候选文件，但都无法执行：\n"
            + "\n".join(f"    {p}" for p in paths[:3])
            + "\n  通常是安装损坏或缺依赖，重装试试：pip install --force-reinstall liepin-cli"
        )

    if not auth_ok():
        return False, (
            f"已安装（{exe}）但未授权。\n"
            "  执行：liepin-cli setup   （扫码登录）\n"
            "  检查：liepin-cli auth status"
        )

    return True, f"就绪：{exe}"


def summary() -> str:
    """一行式状态，供主流程打印。"""
    ok, msg = doctor()
    return msg if ok else f"不可用 —\n{msg}"
