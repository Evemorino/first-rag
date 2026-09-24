"""scripts/baseline_check.py 的单元测试。

它守的是"文档里的变异分数别变成摆设"这件事。一个检查器自己要是错了，
就没人再信它报的东西了 —— 所以三件关键事都得测准：统计 mutants、
解析文档、判定是否过期。
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import baseline_check  # noqa: E402  (先补 sys.path 才能导入)

# 和 README 里那句话的排版保持一致（跨行 + 两个空格缩进），
# 因为 --update 是靠分组原样保留空白的，测试必须覆盖到这点。
BASELINE_SENTENCE = (
    "当前基线：383 个变异体\n"
    "  被杀死、71 个存活、5 个无测试覆盖，**变异分数 84.4%**。\n"
)


def make_mutants(tmp_path, per_file):
    """按 {文件名: [exit_code, ...]} 造一个 mutants/src/。"""
    mutants = tmp_path / "mutants"
    (mutants / "src").mkdir(parents=True)
    for name, codes in per_file.items():
        payload = {"exit_code_by_key": {f"k{i}": c for i, c in enumerate(codes)}}
        (mutants / "src" / f"{name}.py.meta").write_text(json.dumps(payload))
    return mutants


def make_doc(tmp_path, sentence=BASELINE_SENTENCE):
    doc = tmp_path / "README.md"
    doc.write_text(f"# 项目\n\n{sentence}\n", encoding="utf-8")
    return doc


# --- 统计：exit_code_by_key 的 1/0/其他分别对应 杀死/存活/无测试 ---


def test_read_mutants_counts_each_outcome(tmp_path):
    mutants = make_mutants(tmp_path, {"ids": [1, 1, 1, 0, 0, 33]})
    counts = baseline_check.read_mutants(mutants)
    assert (counts.killed, counts.survived, counts.no_tests) == (3, 2, 1)
    assert counts.total == 6


def test_score_ignores_mutants_without_tests(tmp_path):
    # 33 = 没有测试覆盖到它，不该被算进分母，否则分数会被无谓地压低。
    mutants = make_mutants(tmp_path, {"ids": [1, 1, 1, 0, 33, 33]})
    counts = baseline_check.read_mutants(mutants)
    assert counts.score == 75.0  # 3 / (3+1)，不是 3/6


def test_read_mutants_across_files(tmp_path):
    mutants = make_mutants(tmp_path, {"ids": [1, 0], "sync": [0, 1, 1]})
    counts = baseline_check.read_mutants(mutants)
    assert (counts.killed, counts.survived) == (3, 2)


def test_read_mutants_without_data(tmp_path):
    assert baseline_check.read_mutants(tmp_path / "nope") is None


def test_third_bucket_is_not_one_thing(tmp_path):
    """第三桶是一堆不同意思：33=无测试，-11=段错误，-24=超时；而 3 也是"杀死"。

    2026-09-24 实测那轮的"24 个无测试覆盖"里，只有 10 条真是 no tests，另 10 条
    段错误、4 条超时。工具把它们报成同一件事，读者就会拿"补测试"去对待崩溃和
    超时 —— 分类必须跟着 mutmut 自己的 status_by_exit_code 走，别在本地重抄一套。
    """
    mutants = make_mutants(tmp_path, {"ids": [1, 3, 0, 33, -11, -24]})

    counts = baseline_check.read_mutants(mutants)
    breakdown = baseline_check.unchecked_breakdown(mutants)

    assert (counts.killed, counts.survived, counts.no_tests) == (2, 1, 3)
    assert breakdown == {"no tests": 1, "segfault": 1, "timeout": 1}


def test_main_prints_the_third_buckets_composition(tmp_path, capsys):
    """报数的时候就得把构成打出来，不能等人自己去看 .meta。"""
    mutants = make_mutants(tmp_path, {"ids": [1, 0, 33, -11, -11]})
    doc = make_doc(tmp_path, "1 个变异体被杀死、1 个存活、3 个无测试覆盖，**变异分数 50.0%**")

    baseline_check.main(
        ["--mutants", str(mutants), "--doc", str(doc)])

    out = capsys.readouterr().out
    assert "无测试 1" in out and "段错误 2" in out


# --- 解析文档 ---


def test_parse_doc(tmp_path):
    counts = baseline_check.parse_doc(make_doc(tmp_path))
    assert (counts.killed, counts.survived, counts.no_tests) == (383, 71, 5)


def test_parse_doc_without_baseline(tmp_path):
    assert baseline_check.parse_doc(make_doc(tmp_path, "没有基线这句话。\n")) is None


def test_parse_doc_ignores_other_percentages(tmp_path):
    # README 别处也有 84.4%（讲假杀那段），只有"个无测试覆盖 … 变异分数"这句才是基线。
    sentence = BASELINE_SENTENCE + "分数会从 84.4% 虚高到假的 96.9%。\n"
    counts = baseline_check.parse_doc(make_doc(tmp_path, sentence))
    assert counts.killed == 383


# --- 判定与退出码 ---


def test_main_consistent_is_zero(tmp_path, capsys):
    doc = make_doc(tmp_path)
    mutants = make_mutants(tmp_path, {"ids": [1] * 383 + [0] * 71 + [33] * 5})
    assert baseline_check.main(["--doc", str(doc), "--mutants", str(mutants)]) == 0
    assert "基线一致" in capsys.readouterr().out


def test_main_stale_doc_is_one(tmp_path, capsys):
    doc = make_doc(tmp_path)
    mutants = make_mutants(tmp_path, {"ids": [1] * 10 + [0] * 10})
    assert baseline_check.main(["--doc", str(doc), "--mutants", str(mutants)]) == 1
    assert "已过期" in capsys.readouterr().out


def test_main_without_mutants_skips(tmp_path, capsys):
    doc = make_doc(tmp_path)
    exit_code = baseline_check.main(
        ["--doc", str(doc), "--mutants", str(tmp_path / "nope")]
    )
    assert exit_code == 0
    assert "跳过核对" in capsys.readouterr().out


# --- --update ---


def test_update_keeps_layout_and_numbers(tmp_path):
    doc = make_doc(tmp_path, "当前基线：370 个变异体\n  被杀死、80 个存活、9 个无测试覆盖，**变异分数 82.2%**。\n")
    baseline_check.update_doc(baseline_check.Counts(383, 71, 5), doc)
    text = doc.read_text(encoding="utf-8")
    # 排版（换行与缩进）必须原样保留，否则每次刷新都会产生无意义的大 diff
    assert "383 个变异体\n  被杀死、71 个存活、5 个无测试覆盖" in text
    assert "**变异分数 84.4%**" in text
    assert "370" not in text and "82.2" not in text


def test_update_refuses_ambiguous_doc(tmp_path):
    doc = make_doc(tmp_path, BASELINE_SENTENCE + BASELINE_SENTENCE)
    with pytest.raises(SystemExit) as excinfo:
        baseline_check.update_doc(baseline_check.Counts(1, 1, 1), doc)
    assert str(excinfo.value) == "在 {} 里找到 2 处基线，期望恰好 1 处，未改动".format(doc)
