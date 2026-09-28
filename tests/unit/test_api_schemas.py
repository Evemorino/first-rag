"""响应模型与核心层视图、以及 openapi 快照的一致性（T129）。

为什么这三条判据值得单独立一个文件：`response_model` 是**白名单**，它错误的表现
不是报错，而是"悄悄少一个字段"—— 前端拿到 `undefined`，界面少一块，谁都不会红。
所以这里把三件事钉住：

1. 模型字段集 == `entries.to_view` 实际返回的键集（防模型落后于核心层）；
2. 详情模型 == 视图 + 同日邻居两个键（防把两种形状合成一个）；
3. 读端点在 openapi 里指向**具名** schema（防 response_model 被拿掉之后，
   生成的 TS 退回 `{[key: string]: unknown}` —— 那等于前端没有类型）。
"""

from src import entries
from src.api import schemas
from src.api.app import app


def test_entry_view_model_matches_what_the_core_returns():
    """模型与 `to_view` 的键集必须逐一对齐 —— 多一个少一个都是漂移。"""
    assert set(schemas.EntryView.model_fields) == set(entries.to_view("x", {}))


def test_detail_model_is_the_view_plus_neighbours():
    """详情比列表行多的就只有同日邻居；合并成一个模型会让列表凭空多两个 null。"""
    extra = set(schemas.EntryDetailView.model_fields) - set(schemas.EntryView.model_fields)

    assert extra == {"prev_id", "next_id"}


def test_read_endpoints_declare_named_response_models():
    """没有具名模型，`make ui-types` 生成出来的就只有 `{[key: string]: unknown}`。"""
    document = app.openapi()
    schemas_in_doc = document["components"]["schemas"]

    for model in ("EntryView", "EntryDetailView", "EntryListView", "TypesView"):
        assert model in schemas_in_doc, f"openapi 里没有具名模型 {model}"

    for path, expected in (
        ("/entries", "EntryListView"),
        ("/entries/{entry_id}", "EntryDetailView"),
        ("/types", "TypesView"),
    ):
        ref = (
            document["paths"][path]["get"]["responses"]["200"]["content"]
            ["application/json"]["schema"]["$ref"]
        )
        assert ref == f"#/components/schemas/{expected}", f"{path} 指向了 {ref}"
