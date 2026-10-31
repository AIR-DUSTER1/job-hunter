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
    "安装猎聘 CLI 后可绕过浏览器风控取数：\n"
    "  1) 安装：pip install liepin-cli（或从源码仓库根目录按 README 安装）\n"
    "  2) 首次授权：liepin-cli setup\n"
    "  3) 确认状态：liepin-cli auth status\n"
    "装好后重跑采集即可自动切换，无需改代码。"
)


def _resolve_cmd() -> str | None:
    """定位 liepin-cli 可执行文件。

    Windows 上常见的是 .cmd / .exe 包装器，Python 的 subprocess 不会像 shell
    那样自动补扩展名，所以必须显式找到带扩展名的那个。
    """
    # 1) PATH 上带扩展名的
    for name in ("liepin-cli.exe", "liepin-cli.cmd", "liepin-cli.bat", "liepin-cli"):
        p = which(name)
        if p:
            return p
    # 2) 常见安装位置
    import os
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, "bin", "liepin-cli.cmd"),
        os.path.join(home, "bin", "liepin-cli.exe"),
        os.path.join(home, ".zcode", "workspace", "default", "liepin-cil",
                     ".venv", "Scripts", "liepin-cli.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


_EXE: str | None = None


def _exe() -> str | None:
    global _EXE
    if _EXE is None:
        _EXE = _resolve_cmd() or ""
    return _EXE or None


def available() -> bool:
    """liepin-cli 是否可用（已安装且能执行）。"""
    exe = _exe()
    if not exe:
        return False
    try:
        r = subprocess.run([exe, "--help"], capture_output=True,
                           timeout=25, text=True, encoding="utf-8", errors="replace")
        return r.returncode == 0
    except Exception:
        return False


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
