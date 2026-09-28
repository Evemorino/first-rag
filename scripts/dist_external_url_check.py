#!/usr/bin/env python3
"""构建产物里不许有外部地址（NFR-009 ③：无 CDN、断网可用）。

为什么不能只扫入口 HTML：`GET /` 返回的那段 HTML 只有十来行（挂载点 + 一个 script
标签），而产物的主体是 `assets/*.js|css` —— 外部 CDN、字体、`@import`、
`import("https://…")` 全都藏在 bundle 里。v0.9 之前这条断言只扫入口 HTML，
等于只守住了最容易守住的那一小块，所以有了这条（T128，v0.9 analyze 的 HIGH）。

扫描口径：把每个文件里的 `(?:https?:)?//host` 形状全找出来，**先减去**登记过的
那几条"合法出现"（见 ALLOWED —— 它们是标识符与注释，不是取资源的地址），剩下的
任何一条都算违规。规则是"没登记就红"，理由必须写在 ALLOWED 里 —— 和写入边界的
登记表同构：兜底是拒绝，不是放行。

HTML 不加登记豁免：那是静态资源引用的老窝（script/link/img），一个合法理由都没有。

用法::

    python scripts/dist_external_url_check.py [--dist web/dist]

退出码：0 干净；1 有违规；2 产物不存在（先 `make ui` —— 缺产物是环境问题，
不是"通过"）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIST = REPO_ROOT / "web" / "dist"

# 产物里**允许**出现的地址，逐条带理由。每一条都必须是"标识符或注释"，不是
# "浏览器真会去取的东西" —— 判断标准是：删掉它会导致功能坏掉吗？不会，那就不算资源。
# 新增一条前先问：这是不是又给 CDN 开了一道门？给不出理由就别加，回头改代码。
ALLOWED = (
    ("http://www.w3.org/2000/svg",
     "SVG 命名空间标识符（React 内部常量：createElementNS 的名字，不是取资源的地址）"),
    ("http://www.w3.org/1999/xlink",
     "XLINK 命名空间标识符（同上）"),
    ("http://www.w3.org/1998/Math/MathML",
     "MathML 命名空间标识符（同上）"),
    ("http://www.w3.org/XML/1998/namespace",
     "XML 命名空间标识符（同上）"),
    ("https://react.dev/errors/",
     "React 压缩包里的报错文档链接 —— 只出现在错误字符串里，不会被请求"),
    ("https://tailwindcss.com",
     "Tailwind 生成 CSS 顶部那行 license 注释"),
)

URL_RE = re.compile(r"(?:https?:)?//[A-Za-z0-9._-]+")

# HTML 是静态资源引用最可能出现的地方，不给任何豁免。
STRICT_SUFFIXES = frozenset({".html", ".htm"})


def offenders(text: str, *, strict: bool = False) -> list[str]:
    """一段文本里的外部地址（已减去登记过的合法出现）。"""
    stripped = text
    if not strict:
        for literal, _reason in ALLOWED:
            stripped = stripped.replace(literal, "")
    return sorted({match.group(0) for match in URL_RE.finditer(stripped)})


def scan_file(path: Path) -> list[str]:
    """扫一个文件；二进制（字体、图片）读不出文本，跳过。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    return offenders(text, strict=path.suffix in STRICT_SUFFIXES)


def scan_dir(root: Path) -> dict[str, list[str]]:
    """扫整个目录，返回 {相对路径: [外部地址…]}（只含有违规的文件）。"""
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        hits = scan_file(path)
        if hits:
            found[path.relative_to(root).as_posix()] = hits
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dist", type=Path, default=DEFAULT_DIST,
                        help="构建产物目录（默认 web/dist）")
    args = parser.parse_args(argv)

    if not args.dist.is_dir():
        print(f"✗ 产物不在：{args.dist} —— 先 `make ui`（产物不入库）", file=sys.stderr)
        return 2

    found = scan_dir(args.dist)
    if not found:
        print(f"✓ {args.dist} 里没有任何外部地址"
              f"（豁免 {len(ALLOWED)} 条标识符/注释）")
        return 0

    print("✗ 构建产物里出现了外部地址（NFR-009 ③：断网也要能用）：", file=sys.stderr)
    for rel, hits in found.items():
        print(f"    {rel}: {', '.join(hits)}", file=sys.stderr)
    print("\n要去掉它；如果它是标识符或注释而不是资源，"
          "就给 scripts/dist_external_url_check.py 的 ALLOWED 加一条**带理由的**登记。",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
