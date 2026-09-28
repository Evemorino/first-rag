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
  setFilters: (filters: Filters) => void;
  setPage: (page: number) => void;
};

export const useUi = create<UiState>((set) => ({
  filters: emptyFilters,
  page: 1,
  setFilters: (filters) => set({ filters, page: 1 }),
  setPage: (page) => set({ page }),
}));
