"""密钥与令牌的识别规则 —— 一份清单，两处使用。

为什么单独成模块：同一套"什么算密钥"的判断被两个地方需要 ——
运行时脱敏（src/sanitize.py，送进 LLM 之前抹掉）和提交门禁
（scripts/secret_scan.py，别把它提交进库）。各抄一份就会漂，而且**已经漂过
一次**：2026-09-24 之前两边都只认 `sk-` 家族，而本项目真实用的是方舟的
`ark-<UUID>`，于是一边"脱敏过了"、一边"扫过了"，两个绿灯说的都是假话。

规则本身写成拼接形式（`r"\\bar" + r"k-"`）不是风格问题：整段写出来就正好是
这条规则要拦的形状，本仓库的钩子会先把定义它的那个文件拦下。
"""

from __future__ import annotations

import re

# 形如 `api_key: xxx` / `密码 = xxx` 的赋值式泄漏。
# 键名前允许 `[\w.-]*` 前缀：`\b` 在 `_` 旁边不成立（下划线是单词字符），所以
# 原来那版对 `ARK_API_KEY=…` 完全不匹配 —— 而这正是本项目真实会泄漏的那一种写法。
#
# 值的字符集排除了括号类：第一次全库自扫时，`api_key=config.env("ARK_API_KEY")`
# （★ 客户端里读环境变量的那行）和 `secret = TOKEN_CASES[name]` 这类**代码**全被
# 当成了密钥，29 处命中里一大半是这种。真实令牌不会长成一串带括号的表达式，
# 把它们排除出去只减误报，不减检出。
ASSIGNMENT_SECRET_RE = re.compile(
    r"""(?ix)
    ([\w.-]*(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password|passwd|pwd|密码)\b\s*[:=]\s*)
    (["']?)
    ([^\s"'<>(){}\[\]]{6,})
    \2
    """
)

# 长得像令牌的字符串，不需要上下文就能认出来。
# 首尾限定字母数字、内层允许 - 与 _：真实密钥是 `ark-` + UUID（带连字符），
# 只写 [A-Za-z0-9]{20,} 会漏掉它（这个坑 2026-09-24 踩过一次）。
TOKEN_SECRET_RES = (
    re.compile(r"\bs" + r"k-[A-Za-z0-9][A-Za-z0-9_-]{5,}\b"),
    re.compile(r"\bar" + r"k-[A-Za-z0-9][A-Za-z0-9_-]{18,}[A-Za-z0-9]\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_" + r"[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub" + r"_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAK" + r"IA[0-9A-Z]{16}\b"),
    re.compile(
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
    ),
)

RULE_NAMES = ("assignment", "token")
