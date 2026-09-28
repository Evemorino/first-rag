import { create } from "zustand";

/** UI 状态（筛选条件、页码）—— 服务端状态归 TanStack Query，两者不重叠。 */
export type Filters = {
  date_from: string;
  date_to: string;
  type: string;
  project: string;
  source: string;
  include_deleted: boolean;
};

export const emptyFilters: Filters = {
  date_from: "",
  date_to: "",
  type: "",
  project: "",
  source: "",
  include_deleted: false,
};

type UiState = {
  filters: Filters;
  page: number;
  /** 当前页在屏幕上可见的条目 id（列表渲染时发布）—— j/k 要按**屏幕上的顺序**走。 */
  visibleIds: string[];
  setFilters: (filters: Filters) => void;
  setPage: (page: number) => void;
  setVisibleIds: (ids: string[]) => void;
};

export const useUi = create<UiState>((set) => ({
  filters: emptyFilters,
  page: 1,
  visibleIds: [],
  setFilters: (filters) => set({ filters, page: 1 }),
  setPage: (page) => set({ page }),
  setVisibleIds: (visibleIds) => set({ visibleIds }),
}));

/**
 * 在 id 列表里朝 `delta` 方向挪一格（j/k 快捷键的判定，纯函数便于单测）。

 * 三种边界都有明确取舍：
 * * 列表为空 → null（没得选）；
 * * 当前选中的**不在这一页** → 顺向取第一条、逆向取最后一条（换页/换筛选后的第一次按）；
 * * 已到两端 → **停在原地**（返回当前值），不绕回另一端 —— 绕回会在长列表里制造
 *   "按一下跳到最远那一头"的意外。
 */
export function neighborId(
  ids: string[],
  current: string | null,
  delta: 1 | -1,
): string | null {
  if (ids.length === 0) return null;
  const index = current === null ? -1 : ids.indexOf(current);
  if (index === -1) return delta > 0 ? ids[0] : ids[ids.length - 1];
  const next = index + delta;
  if (next < 0 || next >= ids.length) return current;
  return ids[next];
}
