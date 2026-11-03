# -*- coding: utf-8 -*-
"""运行期路径解析。

代码可以装在 skill 目录（只读），但数据必须落在用户的工作目录，否则每次
升级 skill 都会丢登录态和清单。所以这里统一从环境变量取基准目录：

- `JOBHUNT_HOME`  数据根目录（浏览器 profile、out 产出），默认当前工作目录
- `JOBHUNT_PORT`  调试端口，默认 9334（同时只能有一个进程占用）

没设置环境变量时，回退到代码所在目录的上一级（把 scripts/ 当普通项目跑时
行为与之前一致）。
"""

from __future__ import annotations

import os

_CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def home() -> str:
    """数据根目录。"""
    env = os.environ.get("JOBHUNT_HOME")
    if env:
        os.makedirs(env, exist_ok=True)
        return env
    return _CODE_ROOT


def out_dir() -> str:
    p = os.path.join(home(), "out")
    os.makedirs(p, exist_ok=True)
    return p


def profile_dir() -> str:
    p = os.path.join(home(), ".profile")
    os.makedirs(p, exist_ok=True)
    return p


def diagnose_dir() -> str:
    """结构诊断产出目录（报告 JSON + HTML 快照）。"""
    p = os.path.join(out_dir(), "diagnose")
    os.makedirs(p, exist_ok=True)
    return p


def port() -> int:
    try:
        return int(os.environ.get("JOBHUNT_PORT", "9334"))
    except ValueError:
        return 9334
