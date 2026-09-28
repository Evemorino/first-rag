import { useEffect } from "react";

import { entryHash, readHashId } from "../hash-route";
import { neighborId, useUi } from "../store/ui";

/** 编辑框的锚点：`e` 快捷键要聚焦到它（id 写在 EntryEditor 的 textarea 上）。 */
export const EDITOR_ID = "entry-editor-text";

/**
 * 现在焦点在不在"要收字符"的控件里。

 * 判据不能只看 `<input>`：`<textarea>` / `<select>` / `contenteditable` 一样要收字符。
 * 少了这条守卫，在正文里打 j 就会把选中的条目换掉 —— 而输入框里的 j 是再正常不过的字。
 */
export function isTypingTarget(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null;
  if (!element || !element.tagName) return false;
  if (element.isContentEditable) return true;                      // 真浏览器里的快速判据
  if (["INPUT", "TEXTAREA", "SELECT"].includes(element.tagName)) return true;
  // 再按属性找一次祖先：jsdom 不实现 isContentEditable（只在测试里走得通这条），
  // 而且 `contenteditable=""`（空串也是"可编辑"）不会让 isContentEditable 之外的判据成立。
  return element.closest?.('[contenteditable]:not([contenteditable="false"])') !== null;
}

/**
 * 快捷键（T125）：`j` / `k` 在**当前列表**里上下走，`e` 跳到编辑框，`Esc` 松开焦点。
 *
 * 为什么 j/k 要读列表发布出来的 id 顺序，而不是自己算：顺序由筛选与分页决定
 * （日期倒序 + created_at 倒序，见 `entries.list_entries`），在这里重算就是第二份
 * 真相 —— 一旦口径不同，"按 j 往下走"会跳到屏幕上不是下一行的东西。
 */
export function Shortcuts() {
  const visibleIds = useUi((state) => state.visibleIds);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;   // 别抢浏览器快捷键
      if (isTypingTarget(event.target)) return;                     // 别抢输入

      if (event.key === "j" || event.key === "k") {
        const next = neighborId(visibleIds, readHashId(), event.key === "j" ? 1 : -1);
        if (next && next !== readHashId()) {
          window.location.hash = entryHash(next);
          event.preventDefault();
        }
        return;
      }
      if (event.key === "e") {
        const editor = document.getElementById(EDITOR_ID);
        if (editor) {
          editor.focus();
          event.preventDefault();
        }
        return;
      }
      if (event.key === "Escape") {
        (document.activeElement as HTMLElement | null)?.blur();
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [visibleIds]);

  return null;
}
