"""`tasks.md` 里的需求编号引用必须写明来源（2026-09-28 analyze 的 M1 发现）。

为什么需要它：`tasks.md` 的编号引用原来 **81 处不带来源前缀**（占 76%），而
**PRD 与 spec 的 FR 编号从 FR-013 起就错开**（spec FR-013 ↔ PRD FR-014、
spec FR-024 ↔ PRD FR-026 ……），同一个 `FR-029` 在 PRD 是**软删除**、在 spec 是
**前端门禁**。裸引用只能靠上下文猜，而宪法 I 要求"下游工件 MUST 以编号引用 **PRD**
的条目" —— "猜"这件事本身就是缺陷。另外 spec 根本没有 `AC-`/`NFR-` 编号体系
（它用 SC-），所以裸的 `AC-017` 只能指 PRD，但读的人得先知道这件事。

判据收在多宽的地方（有意为之）：**只判"括号引用的开头"** —— 也就是仓库里占绝对
多数的那种写法 `（PRD NFR-009 ④）`。父注里"见 FR-014""PRD FR-026、FR-027"这类
**列表续写或行文引用**不判：正则没法可靠区分"同源的续写"与"另一份文件的裸引用"，
与其为了判据去改造行文，不如把判据收在能机械判准的那一半上。代价如实记：行文里的
裸高号引用仍可能重新出现，靠 review 兜。
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS = REPO_ROOT / "specs" / "001-learning-memory-rag" / "tasks.md"
SPEC = REPO_ROOT / "specs" / "001-learning-memory-rag" / "spec.md"
PRD = REPO_ROOT / "PRD.md"

# 括号里直接开始的引用：`（FR-014` / `（NFR-009` / `（AC-017`（可带 ② 之类的尾巴）
BARE_PAREN_CITATION_RE = re.compile(r"(?<!PRD )(?<!spec )（(?:FR|NFR|AC)-\d+[a-z]?")
# 带来源前缀的引用：`（PRD FR-014）` / `（spec FR-029）`；也认 `（PRD AC-003；FR-014）`
# 这种同一括号内换分隔符的续写。
PREFIXED_CITATION_RE = re.compile(r"(PRD|spec) ((?:FR|NFR|AC)-\d+[a-z]?)")
CONTINUATION_RE = re.compile(r"[；;、,，/](?:FR|NFR|AC)-\d+")

# --- 版本指针（同一个"写进文档就会腐烂"的家族，2026-09-28 一天内踩了三次）---

# PRD §13 表里的版本行：`| **v0.9.2** | 2026-09-28 | ...`
PRD_VERSION_ROW_RE = re.compile(r"^\| \*\*(v\d+\.\d+\.\d+)\*\* \|", re.MULTILINE)
# spec 的同步头：`当前对齐 **PRD v0.9.2**（...`
SPEC_ALIGNMENT_RE = re.compile(r"当前对齐 \*\*PRD (v\d+\.\d+\.\d+)\*\*")
# PRD 自己的状态行：以 `**状态**` 开头的那一行
PRD_STATUS_RE = re.compile(r"^\*\*状态\*\*：.*$", re.MULTILINE)
# PRD 顶部的版本字段：`**版本**：v0.9.2` —— 历史约定是**跟着 §13 最后一行走**（git 历史里
# v0.7.18→…→v0.7.24→v0.8.0→v0.9.0 每一次修一行就把它一起改）。
PRD_VERSION_FIELD_RE = re.compile(r"^\*\*版本\*\*：(v\d+\.\d+\.\d+)$", re.MULTILINE)


def bare_citations(text: str) -> list[str]:
    """括号里裸开头的引用 —— 这是唯一被判红的一类。"""
    return [match.group(0) for match in BARE_PAREN_CITATION_RE.finditer(text)]


def unresolvable_citations(text: str, prd_text: str, spec_text: str) -> list[str]:
    """带前缀却在那份文件里找不到的编号（手滑写错号、或引错了侧）。"""
    missing: list[str] = []
    for source, identifier in PREFIXED_CITATION_RE.findall(text):
        haystack = prd_text if source == "PRD" else spec_text
        if not re.search(rf"\*?\*?{re.escape(identifier)}\*?\*?", haystack):
            missing.append(f"{source} {identifier}")
    return missing


def identifiers(text: str) -> set[str]:
    """文本里出现过的所有需求编号（不带来源时用作"清单里有吗"的兜底）。"""
    return set(re.findall(r"(?:FR|NFR|AC)-\d+[a-z]?", text))


def latest_prd_version(prd_text: str) -> str:
    """PRD §13 表里**最后**一个版本行 —— 表按时间从上到下写，最后一行就是当前版本。"""
    found = PRD_VERSION_ROW_RE.findall(prd_text)
    assert found, "PRD §13 里一行版本记录都没找到，判据失效了"
    return found[-1]


def alignment_pointer(spec_text: str) -> str | None:
    match = SPEC_ALIGNMENT_RE.search(spec_text)
    return match.group(1) if match else None


# --- 判据本身必须先被证明会红（否则它只是一段永远绿的代码）---


def test_bare_citation_is_flagged():
    assert bare_citations("见（FR-029）那一行") == ["（FR-029"]


def test_prefixed_citation_is_not_flagged():
    assert bare_citations("（PRD FR-029）与（spec FR-029）") == []


def test_a_missing_number_is_reported():
    assert unresolvable_citations("（PRD FR-999）", "FR-001", "FR-001") == [
        "PRD FR-999"
    ]


def test_list_continuation_inside_one_paren_is_not_flagged():
    """`（PRD FR-026、FR-027、NFR-011）` 是仓库里的常见写法：前缀管整个列表。"""
    text = "（PRD FR-026、FR-027、NFR-011）"

    assert bare_citations(text) == []
    assert CONTINUATION_RE.findall(text) == ["、FR-027", "、NFR-011"]


# --- 真实文件 ---


def test_tasks_citations_all_name_their_source():
    offenders = bare_citations(TASKS.read_text(encoding="utf-8"))

    assert not offenders, f"这些引用没写来源（PRD 还是 spec？）：{offenders}"


def test_every_prefixed_citation_resolves():
    missing = unresolvable_citations(
        TASKS.read_text(encoding="utf-8"),
        PRD.read_text(encoding="utf-8"),
        SPEC.read_text(encoding="utf-8"),
    )

    assert not missing, f"这些编号在它指的那份文件里不存在：{missing}"


def test_the_two_numbering_schemes_really_do_diverge():
    """这条是上面所有要求的**前提**：两份文件的编号必须真的不一样，否则前缀就是多余的。

    写成断言而不是注释：哪天 spec 的编号与 PRD 对齐了，这里会红，提醒把判据一起放宽。
    """
    spec_text = SPEC.read_text(encoding="utf-8")
    prd_text = PRD.read_text(encoding="utf-8")
    # spec FR-024 ↔ PRD FR-026（spec 的映射自己写在条目里）；语义不同：一个是审阅页，一个是薄壳。
    assert re.search(r"\*\*FR-024\*\*.*（PRD FR-026", spec_text)
    assert "FR-024** FastAPI 薄壳" in prd_text


# --- 版本指针判据本身（先证明它会红，再看真实文件）---


def test_the_version_checker_notices_a_stale_pointer():
    """合成的"过期指针"必须被抓到 —— 否则这条判据只是永远绿的一段代码。"""
    prd_text = "| **v0.1.0** | 2026-01-01 | x |\n| **v0.9.2** | 2026-09-28 | y |\n"
    stale_spec = "> 同步状态：当前对齐 **PRD v0.9.1**（旧的）"

    assert latest_prd_version(prd_text) == "v0.9.2"
    assert alignment_pointer(stale_spec) == "v0.9.1"


def test_spec_alignment_matches_the_latest_prd_version():
    """spec 的"当前对齐"必须跟着 PRD §13 的最后一行走（一天内漂过三次的那个洞）。"""
    prd_text = PRD.read_text(encoding="utf-8")

    assert alignment_pointer(SPEC.read_text(encoding="utf-8")) == latest_prd_version(prd_text)


def test_the_prd_status_line_mentions_its_own_latest_version():
    """PRD 的状态行（顶部那段版本史）也必须提到最新版本，否则读的人以为还停在上一版。"""
    prd_text = PRD.read_text(encoding="utf-8")
    status = PRD_STATUS_RE.search(prd_text)

    assert status, "PRD 里找不到 `**状态**：` 那一行"
    assert latest_prd_version(prd_text) in status.group(0)


def test_the_prd_version_field_tracks_the_latest_row():
    """顶部 `**版本**` 字段跟的是 §13 最后一行（git 历史里的既有约定），不是需求版本号。"""
    prd_text = PRD.read_text(encoding="utf-8")
    field = PRD_VERSION_FIELD_RE.search(prd_text)

    assert field, "PRD 里找不到 `**版本**：vX.Y.Z` 那一行"
    assert field.group(1) == latest_prd_version(prd_text)
