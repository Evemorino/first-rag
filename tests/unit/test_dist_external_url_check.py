"""`scripts/dist_external_url_check.py` 的单元测试（T128）。

这个脚本本身就是一条门禁，所以它自己也得先被证明"会红"：这里给一个植入的 CDN
地址（必须红），再给一组真正合法的标识符/注释（必须绿）。少了后半边，一个
"见谁报谁"的扫描器也能通过。

真产物那一半在 `tests/unit/test_api_ui.py`（本机没构建就 skip；CI 里那次不可跳过）。
"""

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import dist_external_url_check as check  # noqa: E402  (先补 sys.path 才能导入)

# 真实产物里出现过的那几条（React 的命名空间常量、React 报错链接、Tailwind 的
# license 注释）—— 它们是"合法出现"的样本，必须放行。
LEGITIMATE = (
    'const SVG = "http://www.w3.org/2000/svg";\n'
    'const XLINK = "http://www.w3.org/1999/xlink";\n'
    'const MATH = "http://www.w3.org/1998/Math/MathML";\n'
    'const XML = "http://www.w3.org/XML/1998/namespace";\n'
    'const DOC = "https://react.dev/errors/418";\n'
)
TAILWIND_BANNER = "/*! tailwindcss v4.3.3 | MIT License | https://tailwindcss.com */\n"
CDN = 'import("https://cdn.jsdelivr.net/npm/react@19/+esm");\n'


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_cdn_reference_is_caught(tmp_path):
    """这条是红灯样本：外部地址必须被指出来，而且要说清在哪个文件。"""
    write(tmp_path, "assets/index-abc.js", CDN)

    found = check.scan_dir(tmp_path)

    assert list(found) == ["assets/index-abc.js"]
    assert found["assets/index-abc.js"] == ["https://cdn.jsdelivr.net"]


def test_identifiers_and_license_notes_are_allowed(tmp_path):
    """绿样本：命名空间标识符与 license 注释不是资源引用，放行。"""
    write(tmp_path, "assets/index-abc.js", LEGITIMATE)
    write(tmp_path, "assets/index-abc.css", TAILWIND_BANNER)

    assert check.scan_dir(tmp_path) == {}


@pytest.mark.parametrize("literal", [name for name, _reason in check.ALLOWED])
def test_each_allowlisted_literal_is_really_removed(tmp_path, literal):
    """逐条验证豁免：登记了却不生效，等于给 CDN 留了一道没人知道的门。"""
    assert check.offenders(f'const x = "{literal}";') == []


def test_html_gets_no_allowance(tmp_path):
    """HTML 是静态资源引用的老窝，一个豁免都不给。"""
    write(tmp_path, "index.html", '<img src="http://www.w3.org/2000/svg">')

    assert check.scan_dir(tmp_path) == {"index.html": ["http://www.w3.org"]}


def test_protocol_relative_urls_are_caught(tmp_path):
    """协议相对地址（`//host/x`）在 https 页面上一样会去外部取。"""
    write(tmp_path, "assets/index-abc.js", 'const f = "//fonts.example.com/x.woff2";\n')

    assert check.scan_dir(tmp_path)["assets/index-abc.js"] == ["//fonts.example.com"]


def test_binary_files_are_skipped_without_crashing(tmp_path):
    """字体/图片读不成文本，跳过就好 —— 但不能因为读不了就崩。"""
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "font.woff2").write_bytes(b"\x00\x01\xff\xfehttps://x")

    assert check.scan_dir(tmp_path) == {}


def test_missing_dist_is_an_environment_problem(tmp_path, capsys):
    """缺产物是环境问题（退出码 2），不是"通过" —— 否则谁把 dist 删了就自动绿。"""
    assert check.main(["--dist", str(tmp_path / "nope")]) == 2
    assert "make ui" in capsys.readouterr().err


def test_cli_exits_one_and_names_the_file(tmp_path, capsys):
    write(tmp_path, "assets/index-abc.js", CDN)

    assert check.main(["--dist", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "assets/index-abc.js" in err and "cdn.jsdelivr.net" in err


def test_cli_is_quiet_on_a_clean_dist(tmp_path, capsys):
    write(tmp_path, "assets/index-abc.js", LEGITIMATE)

    assert check.main(["--dist", str(tmp_path)]) == 0
    assert "没有任何外部地址" in capsys.readouterr().out


def test_every_allowlist_entry_carries_a_reason():
    """登记表必须逐条给理由 —— 这是它唯一的门槛（没理由就别加）。"""
    for literal, reason in check.ALLOWED:
        assert literal.startswith(("http://", "https://")), literal
        assert len(reason.strip()) >= 10, f"{literal} 的理由太短了"
