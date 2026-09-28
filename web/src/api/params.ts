import type { Filters } from "../store/ui";

/**
 * UI 形状 → 接口参数的映射。这里只放纯函数（便于单测，T120 起）。
 *
 * 查询串那一半给 `/entries` 读；`splitTags` 那一半给写接口的 payload 用：
 * 输入框里是「逗号分隔的字符串」，接口要的是去重前的字符串数组 —— 这个转换
 * 以前内联在提交处理器里，所以它一个测试都没有。
 */
export function buildEntriesQuery(filters: Filters, page: number): string {
  const params = new URLSearchParams({ page: String(page) });
  for (const key of ["date_from", "date_to", "type", "project", "source"] as const) {
    const value = filters[key].trim();
    if (value) params.set(key, value);
  }
  if (filters.include_deleted) params.set("include_deleted", "true");
  return params.toString();
}

/** 输入框里的「a, b , a」→ `["a", "b", "a"]`（两端空白不算标签，空输入给空数组）。 */
export function splitTags(value: string): string[] {
  return value
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);
}
