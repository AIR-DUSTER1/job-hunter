# -*- coding: utf-8 -*-
"""选择器注册表的读写。

站点 DOM 选择器集中在 `adapters/selectors.json`，适配器 / login / verify 都从
这里取，不再各自硬编码。这样改版时只改配置，且同一选择器只有一份。

两层解析，后一层覆盖前一层：

1. 代码目录里的 `adapters/selectors.json`（随 skill 分发的默认值）
2. `$JOBHUNT_HOME/selectors.json`（用户本地覆盖；代码目录只读时 `--apply` 写这里）

`apply()` 只做落盘，是否值得固化由调用方（diagnose 在真实页面上验证过）判断。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import tempfile

from . import paths

_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "adapters", "selectors.json"
)
_OVERRIDE_PATH = os.path.join(paths.home(), "selectors.json")

_cache: dict | None = None
_cache_path: str | None = None


class SelectorRegistryError(RuntimeError):
    """注册表缺失、字段缺失或结构损坏。"""


def _read(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise SelectorRegistryError(f"选择器注册表不存在：{path}")
    except json.JSONDecodeError as e:
        raise SelectorRegistryError(f"选择器注册表 JSON 损坏（{path}）：{e}")
    if not isinstance(data, dict) or not isinstance(data.get("platforms"), dict):
        raise SelectorRegistryError(f"选择器注册表缺少 platforms 段：{path}")
    return data


def _merge(base: dict, extra: dict) -> dict:
    """把本地覆盖合并进默认值：平台/字段级别覆盖，不整体替换。"""
    base.setdefault("platforms", {})
    for plat, keys in (extra.get("platforms") or {}).items():
        if not isinstance(keys, dict):
            continue
        base["platforms"].setdefault(plat, {}).update(keys)
    for plat, fields in (extra.get("unsupported_fields") or {}).items():
        base.setdefault("unsupported_fields", {})[plat] = fields
    if extra.get("learned"):
        base.setdefault("learned", []).extend(extra["learned"])
    return base


def load(force: bool = False) -> dict:
    """载入注册表（默认 + 本地覆盖），进程内缓存。"""
    global _cache, _cache_path
    if _cache is not None and not force and _cache_path is not None:
        return _cache

    data = _read(_DEFAULT_PATH)
    _cache_path = _DEFAULT_PATH
    if os.path.isfile(_OVERRIDE_PATH):
        try:
            data = _merge(data, _read(_OVERRIDE_PATH))
            _cache_path = _OVERRIDE_PATH
        except SelectorRegistryError:
            # 本地覆盖损坏时退回默认值，不要让整个采集挂掉
            pass
    _cache = data
    return data


def reload() -> dict:
    return load(force=True)


def active_path() -> str:
    """当前生效的落盘目标。"""
    load()
    return _cache_path or _DEFAULT_PATH


def candidates(platform: str, key: str) -> list[str]:
    """取某平台某字段的候选链。字段不存在时报错，并指出修复方式。"""
    data = load()
    plat = (data.get("platforms") or {}).get(platform)
    if plat is None:
        raise SelectorRegistryError(
            f"注册表里没有平台「{platform}」，可选：{', '.join(sorted(data.get('platforms') or {}))}"
        )
    chain = plat.get(key)
    if not chain:
        raise SelectorRegistryError(
            f"平台「{platform}」缺少选择器「{key}」。"
            f"检查 {_DEFAULT_PATH} 的 platforms.{platform} 段，"
            f"或运行 python -m scripts.main diagnose --platforms {platform}"
        )
    if isinstance(chain, str):
        return [chain]
    return [c for c in chain if isinstance(c, str) and c]


def primary(platform: str, key: str) -> str:
    """首选选择器（诊断报告里展示用）。"""
    chain = candidates(platform, key)
    return chain[0] if chain else ""


def unsupported_fields(platform: str) -> set[str]:
    """平台产品层面就不提供的字段，健康检查要跳过。"""
    data = load()
    raw = (data.get("unsupported_fields") or {}).get(platform) or []
    return {f for f in raw if isinstance(f, str) and not f.startswith("_")}


def _atomic_write(path: str, data: dict) -> None:
    """先写临时文件再替换，避免中途崩溃留下半个 JSON。"""
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".selectors-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def apply(platform: str, key: str, selector: str, evidence: dict | None = None) -> tuple[str, list[str]]:
    """把某个字段的首选选择器换成 selector 并落盘。

    返回 (写入路径, 变更前的候选链)。代码目录不可写时自动改写到
    `$JOBHUNT_HOME/selectors.json`（本地覆盖层），这样 skill 升级不会冲突。

    evidence 会连同旧值一起记进 `learned`，便于回溯「为什么改成这个」。
    """
    data = load()
    platform_map = data.setdefault("platforms", {})
    plat = platform_map.get(platform)
    if plat is None:
        raise SelectorRegistryError(f"注册表里没有平台「{platform}」，无法固化选择器")

    old_chain = candidates(platform, key)
    # 新选择器放首位，旧值保留在后面：万一新值以后失效还能回退
    new_chain = [selector] + [c for c in old_chain if c != selector]
    plat[key] = new_chain

    used = (evidence or {}).get("used") or {}
    entry = {
        "at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "platform": platform,
        "key": key,
        "old": old_chain,
        "new": new_chain,
        "evidence": used,
    }
    data.setdefault("learned", []).append(entry)

    target = _DEFAULT_PATH
    if not os.access(os.path.dirname(_DEFAULT_PATH), os.W_OK):
        target = _OVERRIDE_PATH
        # 覆盖层只写这一个字段，避免把默认值整份复制过去后失去同步
        existing: dict = {}
        if os.path.isfile(_OVERRIDE_PATH):
            try:
                existing = _read(_OVERRIDE_PATH)
            except SelectorRegistryError:
                existing = {}
        payload = {k: v for k, v in existing.items() if k != "_comment"}
        payload.setdefault("version", 1)
        payload.setdefault("platforms", {}).setdefault(platform, {})[key] = new_chain
        payload.setdefault("learned", []).append(entry)
        _backup(_OVERRIDE_PATH)
        _atomic_write(_OVERRIDE_PATH, payload)
        reload()
        return _OVERRIDE_PATH, old_chain

    _backup(_DEFAULT_PATH)
    payload = {k: v for k, v in data.items() if k != "_comment"}
    _atomic_write(_DEFAULT_PATH, payload)
    reload()
    return target, old_chain


def _backup(path: str) -> None:
    """落盘前留一份 .bak，人工回滚用。"""
    if os.path.isfile(path):
        try:
            shutil.copyfile(path, path + ".bak")
        except OSError:
            pass


def describe(platform: str) -> dict:
    """某平台全部字段的首选值，诊断报告用。"""
    data = load()
    plat = (data.get("platforms") or {}).get(platform) or {}
    return {k: (v[0] if isinstance(v, list) and v else v) for k, v in plat.items()}
