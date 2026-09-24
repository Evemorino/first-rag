#!/usr/bin/env python3
"""提交物密钥扫描：`detect-private-key` 之外的那一半。

为什么需要：pre-commit 自带的 `detect-private-key` 只认 PEM 私钥，看不见 API
令牌。2026-09-24 实测过一次 —— 一把 46 字符的方舟密钥就躺在**被跟踪**的
`.env.example` 里，14 个钩子没有一个响；当时是靠人眼发现的。运行时脱敏
（src/sanitize.py）也救不了这种情况：它管"送进 LLM 之前抹掉"，不管"别提交进库"。

规则清单与脱敏共用 src/secret_patterns.py 那一份：抄两份必然漂，而这个项目
已经为漂过一次付过钱（两边都只认 sk- 家族，谁都没看见 ark-）。

用法::

    python scripts/secret_scan.py 文件...      # pre-commit 传文件名
    python scripts/secret_scan.py              # 不带参数：扫全部 git 跟踪文件

退出码 0 干净；1 有命中。命中只报"文件:行 + 规则 + 打码预览"，绝不回显密钥
本身 —— 否则这道钩子会把它的报错日志（CI 上还是公开的）变成新的泄漏点。
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.secret_patterns import (  # noqa: E402  (先补 sys.path 才能导入)
    ASSIGNMENT_SECRET_RE,
    TOKEN_SECRET_RES,
)

# assignment 排前面：它带键名上下文，报出来比"某处一段高熵串"更可行动。
RULES = (("assignment", ASSIGNMENT_SECRET_RE),
         *(("token", pattern) for pattern in TOKEN_SECRET_RES))

SKIP_DIRS = ("data/", "notes/", ".venv/", "mutants/", ".gate-selftest/")
SKIP_NAMES = {"coverage.xml", "pipeline.lock", "uv.lock"}


@dataclass(frozen=True)
class Hit:
    """一次命中：行号、规则名、以及打过码的预览。"""

    line: int
    rule: str
    preview: str


def should_skip(rel: str) -> bool:
    """运行时产物与生成物不扫：它们本来就不进库，扫了只会制造噪声。"""
    if rel.startswith(SKIP_DIRS):
        return True
    return Path(rel).name in SKIP_NAMES


def _mask(value: str) -> str:
    """留首尾各几个字符，够人认出"是哪一处"，又不够拼回原值。"""
    if len(value) <= 10:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 7)}{value[-3:]}"


def scan_text(text: str, assignment_exempt: bool = False) -> list[Hit]:
    """扫一段文本。`assignment_exempt=True` 时跳过赋值式规则。

    键名=值 这种形状出现在源码与文档里通常是代码或引用（`api_key=config.env(…)`），
    出现在 `.env` / yaml / json / toml 里才是事故 —— 当初那把真钥匙就是躺在
    `.env.example` 里的。令牌形态的规则不受这个豁免：哪里硬编码真令牌都得响。
    """
    rules = RULES if not assignment_exempt else tuple(
        (name, pattern) for name, pattern in RULES if name != "assignment"
    )
    hits: list[Hit] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line:
            continue
        for rule, pattern in rules:
            for match in pattern.finditer(line):
                hits.append(Hit(lineno, rule, _preview(rule, match)))
    return _dedupe(hits)


def _preview(rule: str, match) -> str:
    if rule != "assignment":
        return _mask(match.group(0))
    key, value = match.group(1).strip(), match.group(3)
    return f"{key}{_mask(value)}"


def _dedupe(hits: list[Hit]) -> list[Hit]:
    """同一行同一条命中只报一次：赋值式的值往往同时也是一枚令牌。"""
    seen: set[tuple[int, str, str]] = set()
    unique: list[Hit] = []
    for hit in hits:
        key = (hit.line, hit.rule, hit.preview)
        if key not in seen:
            seen.add(key)
            unique.append(hit)
    return unique


# 赋值式规则对这些后缀豁免：源码里的 `api_key=…` 是代码，文档里的是引用配置写法。
# `.env.example` 的 suffix 是 ".example"、`.env` 没有 suffix，两者都不在豁免名单里。
ASSIGNMENT_EXEMPT_SUFFIXES = frozenset({".py", ".md", ".rst", ".txt"})


def scan_file(path: Path) -> list[Hit]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []          # 二进制读不了，也不是文本提交物
    return scan_text(text, assignment_exempt=path.suffix in ASSIGNMENT_EXEMPT_SUFFIXES)


def tracked_files() -> list[str]:
    """`git ls-files` —— 这道钩子问的是"提交物里有没有密钥"，不是"磁盘上有没有"。"""
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT,
        check=True, capture_output=True,
    )
    return [name for name in out.stdout.decode().split("\0") if name]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv[1:])
    targets = args or tracked_files()
    found = 0
    for rel in targets:
        if should_skip(rel):
            continue
        for hit in scan_file(REPO_ROOT / rel):
            print(f"{rel}:{hit.line} [{hit.rule}] {hit.preview}")
            found += 1
    if found:
        print(f"\n✗ 提交物里发现 {found} 处疑似密钥/令牌（宪法 V：密钥只从 .env 读，"
              f"不进代码、配置模板与任何提交物）。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
