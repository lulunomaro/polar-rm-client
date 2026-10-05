"""**绿灯必须比它所验证的代码新** —— 否则那不是绿灯，那是上一次的绿灯。

第 162 轮与第 159 轮，两次事故都是同一件事：

* **第 159 轮**：几个变异脚本按顺序跑在同一个文件上，其中一个把一份旧原文写了回来。
  **流水线的绿灯是真的 —— 它绿在改动之前。**
* **第 162 轮**：测试返回 `0`、流水线返回 `101`，**而那次提交照样做成了。**

所以这个脚本不接受「我跑过了」这句话，**它只接受一件事：**
**最近一次验证的退出码是 0，**并且**那次验证发生在最后一个源文件被改之后。**

用法（作为 `pre-commit` 钩子跑）::

    python tools/green_stamp.py --record   # 验证通过后盖章，退出码必须为 0
    python tools/green_stamp.py --check    # 提交前调用：退出码非 0 就拒绝提交

⚠ **它不负责跑验证** —— 那是 `tools/verify_all.py` 与 `tools/run_checks.ps1` 的事。
**它只回答一个问题：上一次验证是绿的、且绿在当前这版代码上吗？**
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time

# ⚠ **第 164 轮：可以被指定**。
# **Rust 仓不在这个文件所在的树里** —— 不指定就会监控错了那个树。
# **第一版缺少它，结果是 Rust 那张章监控的是 Python 的源文件**（实测）。
ROOT = pathlib.Path(os.environ.get("GREEN_ROOT") or pathlib.Path(__file__).resolve().parent.parent)
STAMP = ROOT / ".green-stamp.json"

# 改了就必须重新验证的目录。
WATCH = ("rm_client", "tests", "tools", "crates", "docs", "scripts", "config")
# 这些不是源，改了不该作废绿灯。
IGNORE_SUFFIX = (".pyc", ".pyo", ".log", ".tmp", ".json.tmp")
IGNORE_PARTS = ("__pycache__", "target", ".git", ".audit_scratch", "node_modules")


def _stamp_path() -> pathlib.Path:
    """允许 `GREEN_STAMP` 覆盖路径 —— Rust 仓的流水线会从另一个仓调用它。"""
    override = os.environ.get("GREEN_STAMP")
    return pathlib.Path(override) if override else STAMP


def _newest_source_mtime() -> tuple[float, str]:
    newest, where = 0.0, ""
    for name in WATCH:
        base = ROOT / name
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix in IGNORE_SUFFIX or any(p in IGNORE_PARTS for p in path.parts):
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime > newest:
                newest, where = mtime, path.relative_to(ROOT).as_posix()
    return newest, where


def record(exit_code: int, label: str) -> int:
    """验证跑完之后调用。**只有退出码 0 才盖章** —— 非零要把旧章毁掉。"""
    path = _stamp_path()
    if exit_code != 0:
        # ⚠ **验证失败 ⇒ 撤章，而不是留旧章。**
        # 留着旧章就是第 159 轮那次事故：绿灯是真的，绿在改动之前。
        if path.exists():
            path.unlink()
            print(f"[green-stamp] \u9000\u51fa\u7801 {exit_code}\uff08{label}\uff09\uff0c\u65e7\u7ae0\u5df2\u64a4\u6389")
        else:
            print(f"[green-stamp] \u9000\u51fa\u7801 {exit_code}\uff08{label}\uff09\uff0c\u672a\u76d6\u7ae0")
        return 0
    newest, where = _newest_source_mtime()
    path.write_text(
        # ⚠ **第 169 轮：原来是 `%.6f`** —— **而 mtime 的精度高于微秒**，
        # `%.6f` 会**向下取整**，所以刚盖的章**比自己记录的值小**，
        # **可 `--check` 永远拒绝**。**第 169 轮实测到了这个。**
        # 改用 `%.9f`（微秒恰好够）。
        '{"at": %.9f, "newest": %.9f, "where": %s, "label": %s}\n'
        % (time.time(), newest, json_quote(where), json_quote(label)),
        encoding="utf-8",
    )
    print(f"[green-stamp] \u5df2\u76d6\u7ae0\uff1a{label}  exit=0  \u6700\u65b0\u6e90\u6587\u4ef6 {where}")
    return 0


def json_quote(text: str) -> str:
    import json

    return json.dumps(text, ensure_ascii=False)


def check() -> int:
    import json

    path = _stamp_path()
    if not path.exists():
        print("[green-stamp] \u62d2\u7edd\uff1a**\u4ece\u6ca1\u6709\u76d6\u8fc7\u7ae0** \u2014\u2014 \u5148\u8dd1\u9a8c\u8bc1\u5e76\u8c03 `--record`\u3002")
        return 1
    try:
        stamp = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print(f"[green-stamp] \u62d2\u7edd\uff1a\u7ae0\u4e0d\u53ef\u8bfb\uff08{exc}\uff09")
        return 1

    newest, where = _newest_source_mtime()
    if newest > float(stamp.get("newest", 0.0)):
        print(
            "[green-stamp] \u62d2\u7edd\uff1a**\u6700\u65b0\u7684\u6e90\u6587\u4ef6\u6bd4\u7ae0\u65b0**\n"
            f"           \u6700\u65b0\u53d8\u52a8\uff1a{where}\n"
            f"           \u76d6\u7ae0\u65f6\u7684\u6700\u65b0\u6e90\u6587\u4ef6\uff1a{stamp.get('where', '?')}\n"
            "           **\u90a3\u4e2a\u7eff\u706f\u7eff\u5728\u6539\u52a8\u4e4b\u524d** \u2014\u2014 \u91cd\u8dd1\u9a8c\u8bc1\u5e76\u8c03 `--record`\u3002"
        )
        return 1
    print(f"[green-stamp] \u901a\u8fc7\uff1a\u6700\u8fd1\u4e00\u6b21\u9a8c\u8bc1\uff08{stamp.get('label', '?')}\uff09\u662f\u7eff\u7684\uff0c\u4e14\u76d6\u5728\u6700\u540e\u4e00\u6b21\u6539\u52a8\u4e4b\u540e\u3002")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="\u7eff\u706f\u5728\u6240\u6709\u5b83\u9a8c\u8bc1\u7684\u4ee3\u7801\u4e4b\u540e")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--record", action="store_true", help="\u9a8c\u8bc1\u8dd1\u5b8c\u540e\u76d6\u7ae0")
    group.add_argument("--check", action="store_true", help="\u63d0\u4ea4\u524d\u6821\u9a8c")
    parser.add_argument("--exit-code", type=int, default=0, help="\u9a8c\u8bc1\u7684\u9000\u51fa\u7801")
    parser.add_argument("--label", default="", help="\u7528\u4e8e\u8bb0\u5f55\u7684\u540d\u5b57")
    args = parser.parse_args(argv)
    return record(args.exit_code, args.label or "verify") if args.record else check()


if __name__ == "__main__":
    raise SystemExit(main())
