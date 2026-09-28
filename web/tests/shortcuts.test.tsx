// .tsx：下面要挂载 <Shortcuts />（Provider 包装是 JSX）。
import { fireEvent, render } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { isTypingTarget, Shortcuts } from "../src/components/Shortcuts";
import { neighborId, useUi } from "../src/store/ui";

/**
 * 快捷键（T125）的判据。这些行为在浏览器里"看"不出来：按 j 有没有跳、在输入框里按 j
 * 会不会把选中的条目换掉 —— 后者尤其危险（用户正在打字，选中项被换走），所以那条守卫
 * 必须有自己的测试，而不是靠"写了 if 就行"。
 */
describe("isTypingTarget：哪些控件里不该抢键", () => {
  it("input / textarea / select 都算", () => {
    for (const tag of ["input", "textarea", "select"]) {
      expect(isTypingTarget(document.createElement(tag))).toBe(true);
    }
  });

  it("普通元素与 window 不算（否则快捷键等于没有）", () => {
    expect(isTypingTarget(document.createElement("div"))).toBe(false);
    expect(isTypingTarget(window)).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
  });

  it("contenteditable 也算 —— 它一样收字符", () => {
    // 用属性而不是 `div.contentEditable = "true"`：jsdom 不实现那个属性访问器，
    // 而真实页面里两种写法等价（属性是真相，属性访问器只是它的反射）。
    const div = document.createElement("div");
    div.setAttribute("contenteditable", "true");
    expect(isTypingTarget(div)).toBe(true);
  });

  it("嵌在 contenteditable 里的子元素也算（空串也是「可编辑」）", () => {
    const host = document.createElement("div");
    host.setAttribute("contenteditable", "");
    const inner = document.createElement("span");
    host.append(inner);

    expect(isTypingTarget(inner)).toBe(true);
  });
});

describe("neighborId：j/k 的边界取舍", () => {
  const ids = ["a", "b", "c"];

  it("顺向 / 逆向各挪一格", () => {
    expect(neighborId(ids, "a", 1)).toBe("b");
    expect(neighborId(ids, "c", -1)).toBe("b");
  });

  it("没选中或选中的不在这一页：顺向取第一条、逆向取最后一条", () => {
    expect(neighborId(ids, null, 1)).toBe("a");
    expect(neighborId(ids, "别的页的 id", 1)).toBe("a");
    expect(neighborId(ids, null, -1)).toBe("c");
  });

  it("到两端就停住，不绕回另一端", () => {
    expect(neighborId(ids, "c", 1)).toBe("c");
    expect(neighborId(ids, "a", -1)).toBe("a");
  });

  it("空列表没有可选的", () => {
    expect(neighborId([], "a", 1)).toBeNull();
  });
});

describe("按 j / k 真的会换选中项", () => {
  beforeEach(() => {
    useUi.setState({ visibleIds: ["a", "b", "c"] });
    window.location.hash = "";
  });

  it("从没选中开始，j 落到第一条，再按一次到第二条", () => {
    render(<Shortcuts />);

    fireEvent.keyDown(window, { key: "j" });
    expect(window.location.hash).toBe("#/entry/a");

    fireEvent.keyDown(window, { key: "j" });
    expect(window.location.hash).toBe("#/entry/b");
  });

  it("k 往回走", () => {
    window.location.hash = "#/entry/b";
    render(<Shortcuts />);

    fireEvent.keyDown(window, { key: "k" });
    expect(window.location.hash).toBe("#/entry/a");
  });

  it("到了末尾再按 j 不绕回开头", () => {
    window.location.hash = "#/entry/c";
    render(<Shortcuts />);

    fireEvent.keyDown(window, { key: "j" });
    expect(window.location.hash).toBe("#/entry/c");
  });

  it("输入框里按 j 不动选中项", () => {
    render(<Shortcuts />);
    const input = document.createElement("input");
    document.body.append(input);

    fireEvent.keyDown(input, { key: "j" });

    expect(window.location.hash).toBe("");
    input.remove();
  });
});
