"""提交物密钥扫描（scripts/secret_scan.py）的单测。

为什么要有这道钩子：`detect-private-key` 只认 PEM 私钥，看不见 API 令牌。
2026-09-24 实测过一次：一把 46 字符的方舟密钥就躺在**被跟踪**的 `.env.example`
里，14 个钩子没有一个响。运行时脱敏（src/sanitize.py）也救不了这种情况 ——
它管的是"送进 LLM 前抹掉"，不管"别提交进库"。

被测的是纯函数部分（scan_text / should_skip），不落盘、不起子进程：文件遍历
那半边只是 IO，把判断拆出来才测得到规则本身。

与 test_write_boundary.py 同一条约定：先补 sys.path 再直接 import 脚本，
这样不牵扯 src/ 的依赖链（不需要 .env，也不需要 Qdrant）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import secret_scan  # noqa: E402  (先补 sys.path 才能导入)

scan_text = secret_scan.scan_text
should_skip = secret_scan.should_skip

# 一律拼接构造：整段写出来会被这道钩子自己（以及别处的敏感内容扫描）拦下，
# 而这里需要的只是一个能命中规则的形状。
_ARK = "ar" + "k-"
_SK = "s" + "k-"

LEAKY_CASES = {
    "ark_plain": _ARK + "7" * 42,
    "ark_uuid": (_ARK + "1" * 8 + "-" + "2" * 4 + "-"
                 + "3" * 4 + "-" + "4" * 4 + "-" + "5" * 12),
    "sk_plain": _SK + "proj" + "a" * 30,
    "assignment": "ARK_API_KEY=" + "m" * 24,
}


def test_each_leaky_shape_is_reported():
    """四种形态逐个点名 —— 只测一种的话，其余几种漏了也不会红。"""
    for name, text in LEAKY_CASES.items():
        hits = scan_text(text)
        assert hits, f"{name} 没被扫出来"
        assert all(hit.line == 1 for hit in hits), name


def test_hit_reports_line_number_and_masked_preview():
    """报错要指得准，但不能把密钥本身再打印一遍到终端和 CI 日志里。"""
    secret = _ARK + "9" * 40
    hits = scan_text(f"line one\nstill fine\nKEY = {secret}\n")

    assert len(hits) == 1
    hit = hits[0]
    assert hit.line == 3
    assert secret not in hit.preview, "报错信息把密钥原文又打印了一遍"
    assert "*" in hit.preview


def test_clean_text_has_no_hits():
    text = "\n".join([
        "ARK_API_KEY=",                        # 模板里的空值
        "ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/plan/v3",
        "文档里写成 ARK_API_KEY=<key> 也不该中",
        "renamed long-task-coordinator.ts and task-lifecycle.ts",
        "landmark-2 marker, spark-plug notes, hyper-token style",
    ])
    assert scan_text(text) == []


def test_empty_value_assignment_is_not_a_secret():
    """`.env.example` 的每个键都是空的：钩子必须放行，否则改一次模板红一次。"""
    assert scan_text("ARK_API_KEY=\nCHAT_MODEL=\n") == []


def test_should_skip_covers_lockfiles_and_runtime_data():
    assert should_skip("uv.lock") is True
    assert should_skip("data/raw/2026-09-18.json") is True  # 已 gitignore，防御性
    assert should_skip("notes/inbox.md") is True
    assert should_skip("src/sanitize.py") is False
    assert should_skip("README.md") is False


# --- 赋值式规则只管配置形态，不管源码与文档 ---


def test_assignment_rule_skips_code_and_prose():
    """`api_key=config.env("ARK_API_KEY")` 是**读环境变量的代码**，不是密钥。

    第一次全库自扫时它被点了名（★ 的 src/ark_client.py:22），而给一个受宪法 VII
    保护的手写模块加"允许标记"注释是更糟的解法。文档同理：README 里写
    `api_key=config.env("…")` 解释规则，结果被自己扫出来一处误报。
    """
    line = 'client = OpenAI(api_key=config.env("ARK_API_KEY"))'

    assert scan_text(line, assignment_exempt=True) == []
    assert scan_text("api_key=configenvabcdefgh")  # 默认（配置形态）要响


def test_token_shapes_are_still_caught_in_exempt_files():
    """豁免只管赋值式规则：源码或文档里硬编码一枚真令牌，必须照样响。"""
    hardcoded = "KEY = " + "'" + _ARK + "4" * 40 + "'"

    hits = scan_text(hardcoded, assignment_exempt=True)

    assert [h.rule for h in hits] == ["token"]


def test_env_template_is_scanned_by_the_assignment_rule(tmp_path):
    """当初的真实事故形态：一把真钥落在被跟踪的 `.env.example` 里。

    这条测的是"按文件名决定要不要用赋值式规则"那半边判断，光测 scan_text
    证明不了 .py/.md 的豁免没有把 .env 一起豁免掉。
    """
    leak = tmp_path / ".env.example"
    leak.write_text("ARK_API_KEY=" + _ARK + "5" * 40 + "\n", encoding="utf-8")
    ok_code = tmp_path / "ok.py"
    ok_code.write_text('KEY = api_key=configenvvalue1\n', encoding="utf-8")

    assert [h.rule for h in secret_scan.scan_file(leak)] == ["assignment", "token"]
    assert secret_scan.scan_file(ok_code) == []
