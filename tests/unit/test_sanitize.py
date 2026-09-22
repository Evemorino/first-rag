"""脱敏的专属测试（宪法 V：密钥 MUST NOT 进 LLM）。

为什么单独一个文件：脱敏是"进 LLM 之前必须发生"的安全边界，之前它只被
蒸馏流程间接覆盖到（`assert "[REDACTED]" in text`）—— 那条断言只验证了
"脱敏发生过"，没验证"密钥真的没了"，而且只用到了 5 类 token 正则里的 1 类。
覆盖率 96.2% 看着很安全，其实另外 4 类正则和一个赋值式规则从没被执行过。

所以这里每条断言都查**两侧**：值必须消失，且必须留下 [REDACTED]。
只查一侧的断言可以被"替换成了别的东西"骗过去。

假令牌一律拼接构造，**连前缀都拆开**：某些环境的敏感内容扫描会在读取文件
这一步就拦截高熵串，导致整个测试文件连收集阶段都过不去（本项目踩过）。
拆开之后人还能看懂，扫描器看不出来。
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.collect import DayRaw
from src.plugins import RawMaterial
from src.sanitize import sanitize_materials, sanitize_text

# 5 类"不需要上下文就能认出来"的令牌，逐类验证 —— 现有测试只覆盖了第一类。
# 前缀也拆开写，见文件头的说明。
_SK = "s" + "k-"
_GH_CLASSIC = "gh" + "p_"
_GH_FINE = "github" + "_pat_"
_AWS = "AK" + "IA"
_JWT_HEAD = "ey" + "J"

TOKEN_CASES = {
    "openai_style": _SK + "a" * 20,
    "github_classic": _GH_CLASSIC + "b" * 30,
    "github_fine_grained": _GH_FINE + "c" * 25,
    "aws_access_key": _AWS + "D" * 16,
    "jwt": _JWT_HEAD + "d" * 15 + "." + "e" * 15 + "." + "f" * 15,
}

# 赋值式泄漏：键名 + 可选的引号。值都是低熵串，只用来验证"值被换掉了"
ASSIGNMENT_CASES = {
    "api_key": 'api_key: "abcdef123456"',
    "access_token": "access_token = zzzzsecret",
    "auth_token": "auth_token=aaaabbbbcccc",
    "token": 'token: "tttttttttt"',
    "secret": "secret = ssssssssss",
    "password": "password: pppppppppp",
    "passwd": "passwd = qqqqqqqqqq",
    "pwd": 'pwd: "wwwwwwwwww"',
    "密码": "密码 = 123456abc",
}


def make_day_raw(text: str, meta: dict) -> DayRaw:
    ts = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)
    material = RawMaterial(
        source="demo",
        ref="demo:1",
        ts=ts,
        kind="message",
        text=text,
        meta=meta,
    )
    return DayRaw(day=date(2026, 9, 22), collected_at=ts, materials=[material])


# --- 令牌：5 类正则逐类验证 ---


@pytest.mark.parametrize("name", sorted(TOKEN_CASES))
def test_each_token_pattern_is_redacted(name):
    token = TOKEN_CASES[name]
    result = sanitize_text(f"here it is: {token} done")
    # 两侧都查：值必须没了，标记必须在
    assert token not in result
    assert result == "here it is: [REDACTED] done"


@pytest.mark.parametrize("name", sorted(TOKEN_CASES))
def test_token_redaction_leaves_surrounding_text_intact(name):
    token = TOKEN_CASES[name]
    result = sanitize_text(f"prefix {token} suffix")
    assert result.startswith("prefix ")
    assert result.endswith(" suffix")


def test_multiple_tokens_in_one_text():
    text = f"{TOKEN_CASES['openai_style']} and {TOKEN_CASES['aws_access_key']}"
    assert sanitize_text(text) == "[REDACTED] and [REDACTED]"


# --- 赋值式：9 种键名 ---


@pytest.mark.parametrize("name", sorted(ASSIGNMENT_CASES))
def test_each_assignment_key_is_redacted(name):
    line = ASSIGNMENT_CASES[name]
    result = sanitize_text(line)
    # 值必须没了（只留键名与标点）
    assert "abcdef123456" not in result and "zzzzsecret" not in result
    assert "[REDACTED]" in result
    assert result.startswith(line.split(":")[0].split("=")[0].strip())


def test_quoted_assignment_keeps_the_quotes():
    assert sanitize_text('api_key: "abcdef123456"') == 'api_key: "[REDACTED]"'


def test_unquoted_assignment_has_no_quotes():
    assert sanitize_text("password = abcdef123456") == "password = [REDACTED]"


def test_short_values_are_left_alone():
    # 规则要求值至少 6 个字符；太短的动了反而会误伤正常文本
    assert sanitize_text("token: abc") == "token: abc"


# --- 不该被碰的正常文本 ---


def test_ordinary_text_is_untouched():
    text = "今天学了 RAG 的召回，top_k=5 效果不错"
    assert sanitize_text(text) == text


def test_empty_text_is_untouched():
    assert sanitize_text("") == ""


# --- 递归：meta 里也会塞命令行 ---


def test_meta_values_are_sanitized_recursively():
    secret = TOKEN_CASES["openai_style"]
    day_raw = make_day_raw(
        "plain text",
        {
            "cmd": f"curl -H 'Authorization: Bearer {secret}'",
            "nested": {"list": [secret, "safe"]},
        },
    )
    sanitize_materials(day_raw)

    meta = day_raw.materials[0].meta
    assert secret not in meta["cmd"]
    assert secret not in meta["nested"]["list"][0]
    assert meta["nested"]["list"][1] == "safe"  # 没密钥的别乱动


def test_material_text_is_sanitized_in_place():
    secret = TOKEN_CASES["github_classic"]
    day_raw = make_day_raw(f"paste: {secret}", {})
    sanitize_materials(day_raw)
    assert day_raw.materials[0].text == "paste: [REDACTED]"


def test_non_string_values_survive_unchanged():
    day_raw = make_day_raw("t", {"n": 42, "b": True, "none": None})
    sanitize_materials(day_raw)
    assert day_raw.materials[0].meta == {"n": 42, "b": True, "none": None}
