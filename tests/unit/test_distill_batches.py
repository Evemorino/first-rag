"""切批逻辑（src/distill_batches.py）的单测。

为什么要切批：单次 chat 往返的输出会被服务端 completion 上限掐断。本机实测
（2026-09-24，/tmp/probe_out.py）：让模型写一份长 JSON，它在 **6894 字符 /
6865 completion tokens** 处停住，`finish_reason='length'`，报
`Unterminated string starting at: line 1 column 6876` —— 与 `sync D=2026-09-18`
那两次失败（8370 / 8651 字符处断）同一签名。所以天花板在**输出**侧：一天喂进去
多少字不是决定因素（1.33M 字符的 09-23 那天一次就过了，234k 的 09-18 反而炸）。
"""

from datetime import datetime

from src.distill_batches import split_for_batches
from src.plugins import RawMaterial


def material(text: str, ref: str = "r") -> RawMaterial:
    return RawMaterial(
        source="claude_code", ref=ref,
        ts=datetime(2026, 9, 18, 10, 0, 0),
        kind="message", text=text, meta={},
    )


def test_every_material_survives_in_original_order():
    """切批不能丢数据，也不能改顺序 —— 丢了就是静默少蒸馏。"""
    mats = [material("x" * 40, ref=f"s{i}") for i in range(5)]

    batches = split_for_batches(mats, max_chars=100)

    assert [m.ref for b in batches for m in b] == [f"s{i}" for i in range(5)]


def test_each_batch_stays_within_budget():
    mats = [material("x" * 60, ref=f"s{i}") for i in range(4)]

    batches = split_for_batches(mats, max_chars=200)

    # 贪婪装箱：第 4 条会让该批到 240 > 200，于是前三条成一批、第四条另起。
    assert [sum(len(m.text) for m in b) for b in batches] == [180, 60]


def test_single_material_bigger_than_budget_keeps_its_own_batch():
    """超预算的单条素材不能丢，也不能拆碎：给它单独一批。

    真实数据里一条会话就有 200000 字符（collect._cap 的上限），比任何合理的批次
    预算都大；"放不下就扔"等于把当天最重要的一条素材静默丢掉。
    """
    big = material("x" * 500, ref="big")
    small = material("x" * 30, ref="small")

    batches = split_for_batches([big, small], max_chars=100)

    assert batches == [[big], [small]]


def test_no_materials_yields_no_batches():
    """空日不发请求：一批都没有时不该产生任何 LLM 往返。"""
    assert split_for_batches([], max_chars=100) == []
