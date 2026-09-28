import type { Filters } from "../store/ui";

/** 把 UI 状态拼成 `/entries` 的查询串 —— 纯函数，便于单测（T120）。 */
export function buildEntriesQuery(filters: Filters, page: number): string {
  const params = new URLSearchParams({ page: String(page) });
  for (const key of ["date_from", "date_to", "type", "project", "source"] as const) {
    const value = filters[key].trim();
    if (value) params.set(key, value);
  }
  if (filters.include_deleted) params.set("include_deleted", "true");
  return params.toString();
}
