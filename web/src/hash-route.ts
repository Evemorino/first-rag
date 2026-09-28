/**
 * 哈希路由：`#/entry/<id>`。页面只有两个面（列表 + 详情），不需要路由器。
 *
 * 单独成模块是因为它有三个使用者（App 的监听、列表的点击、快捷键的读当前值），
 * 抄三份的下场是某天格式改成 `#/entries/<id>` 时漏掉一处 —— 而漏掉那处不报错，
 * 只是"点了没反应"。T125 把快捷键接进来时正是这个局面。
 */

export function readHashId(): string | null {
  const match = window.location.hash.match(/^#\/entry\/(.+)$/);
  return match ? decodeURIComponent(match[1]) : null;
}

export function entryHash(id: string): string {
  return `#/entry/${encodeURIComponent(id)}`;
}
