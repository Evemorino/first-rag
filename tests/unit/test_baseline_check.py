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
    """按 {文件名: [exit_code, ...]} 造一个 mutants/src/。

    名字用真实形状 `<模块>.<函数>__mutmut_N`（一个文件算一个函数）：占位名
    （k0 / k1）在真实数据里不存在，而按函数分组、以及"认不出名字就报错"那条
    检查都靠这个名字，夹具用占位名等于把被测的东西绕过去了。
    """
    mutants = tmp_path / "mutants"
    (mutants / "src").mkdir(parents=True)
    for name, codes in per_file.items():
        payload = {"exit_code_by_key": {
            f"{name}.x_func__mutmut_{i}": c for i, c in enumerate(codes)}}
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


# --- 「整个函数没有测试映射」的登记表 ---
# 这一条查的不是分数，是"变异体压根没被判过"这件事。见 KNOWN_NO_TESTS 上方。


def make_named_mutants(tmp_path, per_file):
    """按 {文件名: {变异体名: 退出码}} 造 mutants/src/。

    名字必须保持真实形状（`<模块>.<函数>__mutmut_N`）：分组靠的就是这个名字前缀，
    用上面 make_mutants 那种 k0/k1 占位名测不出任何东西。
    """
    mutants = tmp_path / "mutants"
    (mutants / "src").mkdir(parents=True, exist_ok=True)
    for name, codes in per_file.items():
        (mutants / "src" / f"{name}.py.meta").write_text(json.dumps({"exit_code_by_key": codes}))
    return mutants


def test_no_tests_only_function_is_flagged(tmp_path):
    """一个函数的变异体全判 no tests → 报出来（2026-09-26 那 13 条的现场）。"""
    mutants = make_named_mutants(tmp_path, {"ingest": {
        "ingest.x__skipped_by_source__mutmut_1": 33,
        "ingest.x__skipped_by_source__mutmut_2": 5,
        "ingest.x_upsert__mutmut_1": 1,
    }})
    assert baseline_check.no_tests_only_functions(mutants) == {"ingest.x__skipped_by_source": 2}
    assert baseline_check.unregistered_no_tests(mutants) == {"ingest.x__skipped_by_source": 2}


def test_registered_no_tests_functions_pass(tmp_path):
    """登记过的（真没测试覆盖的工厂）不算问题 —— 兜底是"报出来"，登记才放行。"""
    mutants = make_named_mutants(tmp_path, {"ingest": {"ingest.x__client__mutmut_1": 33}})
    assert baseline_check.no_tests_only_functions(mutants) == {"ingest.x__client": 1}
    assert baseline_check.unregistered_no_tests(mutants) == {}


def test_partly_judged_function_is_not_flagged(tmp_path):
    """函数里只要有任意一条被判定过，映射就是通的；剩下的 no tests 是"分支没走到"。"""
    mutants = make_named_mutants(tmp_path, {"sync": {
        "sync.x_main__mutmut_1": 1,
        "sync.x_main__mutmut_2": 33,
    }})
    assert baseline_check.no_tests_only_functions(mutants) == {}


def test_functions_are_grouped_separately(tmp_path):
    """按 `<模块>.<函数>` 分组，不把同一个模块里的几条混成一个。"""
    mutants = make_named_mutants(tmp_path, {"ids": {
        "ids.x_content_hash__mutmut_1": 33,
        "ids.x_point_id__mutmut_1": 33,
    }})
    assert baseline_check.no_tests_only_functions(mutants) == {
        "ids.x_content_hash": 1, "ids.x_point_id": 1}


def test_main_fails_on_unregistered_no_tests(tmp_path, capsys):
    """文档数字对得上也要红 —— 这条管的不是数字，是映射。"""
    doc = make_doc(tmp_path, "1 个变异体被杀死、0 个存活、1 个无测试覆盖，**变异分数 100.0%**")
    mutants = make_named_mutants(tmp_path, {"ingest": {
        "ingest.x_upsert__mutmut_1": 1,
        "ingest.x__skipped_by_source__mutmut_1": 33,
    }})

    assert baseline_check.main(["--doc", str(doc), "--mutants", str(mutants)]) == 1
    out = capsys.readouterr().out
    assert "ingest.x__skipped_by_source（1 条）" in out
    assert "mutmut-stats.json" in out      # 修法必须写在报错里，否则等于只报不教
    assert "✓ 基线一致" in out              # 数字本身是对的，红的不是它


def test_update_refuses_while_mapping_is_unregistered(tmp_path, capsys):
    """映射没判完就不该写数字：写出去会让人以为那些变异体已经判过了。"""
    doc = make_doc(tmp_path, "1 个变异体被杀死、0 个存活、1 个无测试覆盖，**变异分数 100.0%**")
    mutants = make_named_mutants(tmp_path, {"ingest": {
        "ingest.x_upsert__mutmut_1": 0,
        "ingest.x__skipped_by_source__mutmut_1": 33,
    }})

    assert baseline_check.main(
        ["--doc", str(doc), "--mutants", str(mutants), "--update"]) == 1
    assert "数字先不写" in capsys.readouterr().out
    assert "100.0%" in doc.read_text(encoding="utf-8")   # 文档原样没动


def test_update_writes_once_mapping_is_clean(tmp_path):
    """映射干净时 --update 照旧写回（别把正常路径也堵死）。"""
    doc = make_doc(tmp_path, "1 个变异体被杀死、0 个存活、1 个无测试覆盖，**变异分数 100.0%**")
    mutants = make_named_mutants(tmp_path, {"ingest": {"ingest.x_upsert__mutmut_1": 1}})
    assert baseline_check.main(["--doc", str(doc), "--mutants", str(mutants), "--update"]) == 0
    assert "变异分数 100.0%" in doc.read_text(encoding="utf-8")


def test_unrecognised_mutant_names_are_counted(tmp_path):
    """名字认不出来要能数出来：分组会全空，这条检查就静默瞎了。"""
    mutants = make_named_mutants(tmp_path, {"ids": {
        "k0": 33,
        "ids.x__cap__mutmut_1": 1,
    }})
    assert baseline_check.unrecognised_mutants(mutants) == 1
    assert baseline_check.no_tests_only_functions(mutants) == {}


def test_main_fails_when_mutant_names_are_unrecognisable(tmp_path, capsys):
    """认不出名字时要红 —— 门禁最坏的死法是一直绿着，而不是报错。"""
    doc = make_doc(tmp_path, "1 个变异体被杀死、0 个存活、0 个无测试覆盖，**变异分数 100.0%**")
    mutants = make_named_mutants(tmp_path, {"ids": {"k0": 1}})

    assert baseline_check.main(["--doc", str(doc), "--mutants", str(mutants)]) == 1
    out = capsys.readouterr().out
    assert "_MUTANT_SUFFIX_RE" in out      # 报错里得写清该改哪儿
    assert "✓ 基线一致" in out              # 数字是对的，红的不是数字


def test_update_refuses_when_names_are_unrecognisable(tmp_path, capsys):
    """分组瞎了就不写数字：那份数字根本没被核对过。"""
    doc = make_doc(tmp_path, "1 个变异体被杀死、0 个存活、0 个无测试覆盖，**变异分数 100.0%**")
    mutants = make_named_mutants(tmp_path, {"ids": {"x_mutmut_1": 1}})

    assert baseline_check.main(
        ["--doc", str(doc), "--mutants", str(mutants), "--update"]) == 1
    assert "分组都瞎了" in capsys.readouterr().out
