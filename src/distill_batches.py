"""按字符预算把一天的素材切成多批（distill 的分批依据）。

为什么需要：单次 chat 往返的**输出**会被服务端 completion 上限掐断，而蒸馏要的
是一整份 JSON。本机实测（2026-09-24）一次请求在 6894 字符处
`finish_reason='length'`，JSON 断在半截字符串里；`sync D=2026-09-18` 就是这么炸
的（断点 8370 / 8651 字符，重试也只是换个地方断）。决定炸不炸的是输出长度，
不是输入长度 —— 输入 1.33M 字符的 09-23 那天一次就过了，234k 的 09-18 反而不行。

所以把一天拆成若干批，每批单独一次往返，条目再合并 —— 单批要输出的 JSON 小了，
就断不了。
"""

from __future__ import annotations

from typing import Iterable

from src.plugins import RawMaterial


def split_for_batches(
    materials: Iterable[RawMaterial], max_chars: int
) -> list[list[RawMaterial]]:
    """贪婪装箱：按原顺序累加，超过预算就另起一批。

    单条素材本身就超预算时给它独立的一批，而不是丢掉或拆碎：一条会话可达
    200000 字符（collect 的 _cap 上限），"放不下就扔"等于静默少蒸馏一天的核心
    素材。
    """
    batches: list[list[RawMaterial]] = []
    current: list[RawMaterial] = []
    used = 0
    for material in materials:
        size = len(material.text)
        if current and used + size > max_chars:
            batches.append(current)
            current, used = [], 0
        current.append(material)
        used += size
    if current:
        batches.append(current)
    return batches
