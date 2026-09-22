"""mutmut 兼容补丁 —— 只在变异测试运行时生效。

背景：mutmut 3.x 的 trampoline 里有这么一句硬断言：

    assert not name.startswith("src.")

它假设大家用"src 作为 import root"的布局（包名是 ids、ask 这类）。
本项目反着来：`src` 本身就是包，全项目写 `from src import ...`，
于是每个变异体都会在统计阶段被这句断言打死。

处理办法：用一份去掉该断言的实现替换掉 record_trampoline_hit。
保留其余逻辑很重要 —— mutmut 靠它建立"哪个测试跑到哪个函数"的映射，
如果像省事那样换成空实现，它会直接报"找不到覆盖变异体的测试"然后收工。

代价：本文件是上游函数的副本，mutmut 升级时需要跟着看一眼。
等上游放宽那条断言，整个文件就可以删掉。

由 tests/conftest.py 在导入期调用；日常跑 pytest 时 MUTANT_UNDER_TEST 不在
环境里，这里直接返回，不做任何改动。
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path


def _record_trampoline_hit(name: str, caller: str | None = None) -> None:
    """mutmut.stats.record_trampoline_hit 的副本，只删掉 src. 那条断言。"""
    from mutmut import stats
    from mutmut.configuration import config

    # mutmut 从文件路径推 mutant key（src/ids.py → ids.xxx），而本项目导入时
    # 模块名是 src.ids。这里把前缀去掉，两边才能对上。
    if name.startswith("src."):
        name = name[len("src."):]

    cfg = config()
    if cfg.max_stack_depth != -1:
        frame = inspect.currentframe()
        depth = cfg.max_stack_depth
        while depth and frame:
            filename = frame.f_code.co_filename
            frame = frame.f_back
            if "pytest" in filename or "hammett" in filename \
                    or "unittest" in filename:
                break
            file_path = Path(filename).resolve(strict=True)
            if any(path in file_path.parents
                   for path in cfg.resolved_mutated_source_paths):
                depth -= 1
        if not depth:
            return

    state = stats.state()
    state._stats.add(name)
    if caller is not None and cfg.track_dependencies:
        state.function_dependencies[name].add(caller)


def apply_if_mutating() -> bool:
    """环境里带 MUTANT_UNDER_TEST 说明是在 mutmut 里跑，才打补丁。"""
    if "MUTANT_UNDER_TEST" not in os.environ:
        return False
    try:
        from mutmut import stats
        from mutmut.mutation import trampoline
    except Exception:  # noqa: BLE001 — 没装 mutmut 就不是变异运行
        return False

    if getattr(trampoline, "_src_layout_compat", False):
        return True

    # (1) 统计阶段：把 "src." 前缀去掉，key 才能和 mutant 名对上。
    trampoline.record_trampoline_hit = _record_trampoline_hit
    stats.record_trampoline_hit = _record_trampoline_hit

    # (2) 变异阶段：trampoline 会比对
    #     mutant_under_test 的 module 部分与 decorated_func.__module__，
    #     前者是 "similarity"，后者是 "src.similarity"，永远不等 ——
    #     结果是每个变异体都被当成"别的模块的变异"而走原函数，
    #     测试跑的是没改过的代码，自然全是 survived。把前缀补回去。
    original_get = trampoline.get_mutant_under_test

    def _get_mutant_under_test() -> str:
        name = original_get()
        if "__mutmut_" in name and not name.startswith("src."):
            return f"src.{name}"
        return name

    trampoline.get_mutant_under_test = _get_mutant_under_test
    trampoline._src_layout_compat = True
    return True
