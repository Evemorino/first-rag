import { useEffect } from "react";

import { entryHash } from "../hash-route";
import { useEntries } from "../queries/entries";
import type { Filters } from "../store/ui";
import { useUi } from "../store/ui";
import { META } from "../ui";

type Props = {
  filters: Filters;
  page: number;
  onPage: (page: number) => void;
  selectedId: string | null;
};

const TH = "sticky top-0 z-10 bg-slate-100 px-3 py-1.5 text-left font-medium dark:bg-slate-700";
const TD = "border-b border-slate-100 px-3 py-2 align-top dark:border-slate-700";

function SkeletonRows() {
  return (
    <div className="flex-1 space-y-2 px-4 py-4" aria-hidden="true">
      {[0, 1, 2, 3, 4, 5].map((row) => (
        <div key={row} className="space-y-1 animate-pulse">
          <div className="h-3 w-40 rounded bg-slate-200 dark:bg-slate-700" />
          <div className="h-4 w-full rounded bg-slate-200 dark:bg-slate-700" />
        </div>
      ))}
    </div>
  );
}

function State({ children, tone = "muted" }: { children: string; tone?: "muted" | "error" }) {
  const color = tone === "error" ? "text-red-600 dark:text-red-400" : META;
  return <p className={`flex-1 px-4 py-6 text-sm ${color}`}>{children}</p>;
}

/**
 * 条目列表（FR-026 的列表面）。
 *
 * 用**真表格**而不是一排 div（T125）：日期、类型、正文是三种不同性质的东西，
 * 表格让它们各占一列、表头能粘住，读长列表时不会忘了哪列是什么；数字列用
 * `tabular-nums`，等宽数字不会随位数抖动。
 *
 * `data-testid="entry-list"` 保留：e2e 用它把"列表里有没有"限定在列表内（v0.9 那条
 * 踩过的坑：详情面板里也有同一段文字）。
 */
export function EntryList({ filters, page, onPage, selectedId }: Props) {
  const { data, isPending, error } = useEntries(filters, page);
  const setVisibleIds = useUi((state) => state.setVisibleIds);

  // j/k 快捷键要按屏幕上的顺序走 —— 顺序由这里发布，别处不重算（见 Shortcuts.tsx）。
  useEffect(() => {
    setVisibleIds(data?.entries.map((entry) => entry.id) ?? []);
  }, [data, setVisibleIds]);

  if (isPending) return <SkeletonRows />;
  if (error) {
    return <State tone="error">{`读取失败：${String(error.message)}（Qdrant 起了吗？make up）`}</State>;
  }
  if (!data || data.entries.length === 0) {
    return <State>没有符合条件的条目 —— 试试放宽日期区间或清空筛选。</State>;
  }

  return (
    <>
      <div className="flex-1 overflow-auto">
        <table data-testid="entry-list" className="w-full border-collapse text-sm">
          <thead className={`${META} [&_th]:border-b [&_th]:border-slate-200 dark:[&_th]:border-slate-600`}>
            <tr>
              <th className={`${TH} w-28`}>日期</th>
              <th className={`${TH} w-24`}>类型</th>
              <th className={TH}>正文</th>
              <th className={`${TH} w-28 text-right`}>来源</th>
            </tr>
          </thead>
          <tbody>
            {data.entries.map((entry) => (
              <tr
                key={entry.id}
                aria-selected={entry.id === selectedId}
                className={`cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-700 ${
                  // 选中态要比 hover 明显：j/k 走位时得一眼看出"现在在哪一条"。
                  // 之前用 bg-blue-50 / dark:bg-slate-700，在深色底上几乎看不出来（截图确认）。
                  entry.id === selectedId ? "bg-blue-100 dark:bg-slate-600" : ""
                }`}
                onClick={() => {
                  window.location.hash = entryHash(entry.id);
                }}
              >
                <td className={`${TD} tabular-nums whitespace-nowrap`}>{entry.date}</td>
                <td className={`${TD} whitespace-nowrap`}>
                  {entry.type}
                  {entry.edited && <span className="ml-1 text-amber-600">已编辑</span>}
                  {entry.deleted_at && <span className="ml-1 text-red-600">已删除</span>}
                </td>
                <td className={`${TD} max-w-0 truncate`}>{entry.text}</td>
                <td className={`${TD} whitespace-nowrap text-right`}>{entry.source}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className={`flex items-center gap-3 border-t border-slate-200 px-4 py-2 dark:border-slate-700 ${META}`}>
        <button
          className="rounded border border-slate-300 px-2 py-0.5 disabled:opacity-40 dark:border-slate-600"
          disabled={data.page <= 1}
          onClick={() => onPage(data.page - 1)}
        >
          上一页
        </button>
        <button
          className="rounded border border-slate-300 px-2 py-0.5 disabled:opacity-40 dark:border-slate-600"
          disabled={data.page * data.page_size >= data.total}
          onClick={() => onPage(data.page + 1)}
        >
          下一页
        </button>
        {/* aria-live：筛选/翻页之后总数会变，读屏用户需要被告知（T125） */}
        <span aria-live="polite">
          第 {data.page} 页 · 共 {data.total} 条（每页 {data.page_size}）
        </span>
        <span className={`ml-auto ${META}`}>j / k 上下选择，e 编辑</span>
      </div>
    </>
  );
}
