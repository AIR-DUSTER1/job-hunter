# -*- coding: utf-8 -*-
"""统一命令行入口。

    python -m scripts.main collect --keywords "Agent开发" --city 青岛
    python -m scripts.main login boss
    python -m scripts.main verify liepin
    python -m scripts.main enrich --limit 10

各子命令原本是独立脚本，这里统一到 `main`，避免用户记四个模块名。
"""

from __future__ import annotations

import sys


def _usage() -> None:
    print(
        "用法：python -m scripts.main <子命令> [参数]\n"
        "\n"
        "子命令：\n"
        "  collect   采集岗位并打分导出（最常用）\n"
        "  enrich    给已有清单补 JD / 地址 / HR活跃等详情\n"
        "  diagnose  诊断站点结构是否变更（采集不到东西时先跑这个）\n"
        "  login     打开登录页等待扫码（boss / zhilian / job51 / liepin）\n"
        "  verify    打开风控验证页等待手动过验证\n"
        "  doctor    检查猎聘 CLI 环境（可选增强，装了字段更全）\n"
        "\n"
        "示例：\n"
        '  python -m scripts.main collect --keywords "Agent开发,前端开发" --city 青岛 --salary 10 20 --years 2\n'
        "  python -m scripts.main login boss\n"
        "  python -m scripts.main enrich --limit 10\n"
        "  python -m scripts.main diagnose --platforms boss\n"
        "  python -m scripts.main diagnose --platforms boss --apply\n"
        "\n"
        "环境变量：\n"
        "  JOBHUNT_HOME  数据目录（登录态 + 产出），默认当前目录\n"
        "  JOBHUNT_PORT  浏览器调试端口，默认 9334\n"
    )


def main() -> int:
    # 兼容原来的直调方式：python -m scripts.main --keywords ... 等同于 collect
    if len(sys.argv) < 2:
        _usage()
        return 2

    sub = sys.argv[1]
    if sub in ("-h", "--help", "help"):
        _usage()
        return 0

    if sub == "collect":
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        from . import collect as collect_main
        return collect_main.main()

    if sub == "enrich":
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        from . import enrich
        return enrich.main()

    if sub == "diagnose":
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        from . import diagnose
        return diagnose.main()

    if sub == "login":
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        from . import login
        return login.main()

    if sub == "verify":
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        from . import verify
        return verify.main()

    if sub in ("doctor", "check"):
        from .adapters import liepin_cli
        ok, msg = liepin_cli.doctor()
        print(f"猎聘 CLI：{'✅ 就绪' if ok else '❌ 不可用'}")
        print(msg)
        print()
        print("说明：CLI 是可选增强。不可用时猎聘会退回浏览器采集，")
        print("      字段少一些，且更容易触发短信验证。")
        return 0 if ok else 1

    # 没给子命令，但带了其他参数：按 collect 处理（向后兼容）
    if sub.startswith("-"):
        from . import collect as collect_main
        return collect_main.main()

    print(f"未知子命令：{sub}\n")
    _usage()
    return 2


if __name__ == "__main__":
    sys.exit(main())
