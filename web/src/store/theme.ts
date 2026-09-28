import { create } from "zustand";

/** 三档主题：跟随系统 / 强制浅色 / 强制深色。 */
export type ThemeMode = "system" | "light" | "dark";

const STORAGE_KEY = "first-rag.theme";

function storedMode(): ThemeMode {
  const value = window.localStorage.getItem(STORAGE_KEY);
  return value === "light" || value === "dark" ? value : "system";
}

function systemPrefersDark(): boolean {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

/** 当前该不该是暗色 —— 纯函数，便于单测（"跟系统"这一档最容易写错）。 */
export function isDark(mode: ThemeMode, systemDark: boolean): boolean {
  return mode === "system" ? systemDark : mode === "dark";
}

/**
 * 把主题落到 `<html>` 上。
 *
 * 为什么是加/去 `.dark` 而不是写内联样式：Tailwind v4 的 class 制变体看的是祖先上的
 * `.dark`（见 index.css 的 `@custom-variant`），写样式属性等于绕开它、只对当前元素生效。
 */
function apply(mode: ThemeMode): void {
  document.documentElement.classList.toggle("dark", isDark(mode, systemPrefersDark()));
}

type ThemeState = {
  mode: ThemeMode;
  setMode: (mode: ThemeMode) => void;
};

export const useTheme = create<ThemeState>((set) => ({
  mode: storedMode(),
  setMode: (mode) => {
    window.localStorage.setItem(STORAGE_KEY, mode);
    apply(mode);
    set({ mode });
  },
}));

/**
 * 启动时同步一次，并订阅系统变化。
 *
 * **系统变化只在"跟随系统"那一档管用** —— 手动选过 light/dark 之后就不该被系统
 * 偏好覆盖（那会让人觉得开关失灵）。
 */
export function watchTheme(): () => void {
  const sync = () => apply(useTheme.getState().mode);
  sync();
  const media = window.matchMedia?.("(prefers-color-scheme: dark)");
  if (!media) return () => {};
  media.addEventListener("change", sync);
  return () => media.removeEventListener("change", sync);
}
