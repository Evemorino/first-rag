import { beforeEach, describe, expect, it } from "vitest";

import { isDark, useTheme } from "../src/store/theme";

/**
 * 主题三档（T125）。"跟随系统"这一档最容易写错：手动选过 light/dark 之后不该再被
 * 系统偏好覆盖（那会让人觉得开关失灵），而 system 档必须跟着走。
 */
describe("isDark：哪一档该是暗色", () => {
  it("system 跟着系统", () => {
    expect(isDark("system", true)).toBe(true);
    expect(isDark("system", false)).toBe(false);
  });

  it("手动档不受系统影响", () => {
    expect(isDark("dark", false)).toBe(true);
    expect(isDark("light", true)).toBe(false);
  });
});

describe("setMode：真的落到 <html> 上", () => {
  beforeEach(() => {
    document.documentElement.classList.remove("dark");
    window.localStorage.clear();
  });

  it("选深色会加 .dark（Tailwind v4 的 class 制变体看的就是它）", () => {
    useTheme.getState().setMode("dark");

    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(window.localStorage.getItem("first-rag.theme")).toBe("dark");
  });

  it("切回浅色会去掉 .dark", () => {
    useTheme.getState().setMode("dark");
    useTheme.getState().setMode("light");

    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(window.localStorage.getItem("first-rag.theme")).toBe("light");
  });
});
