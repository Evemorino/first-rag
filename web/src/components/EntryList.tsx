import { useEntries } from "../queries/entries";
import type { Filters } from "../store/ui";

type Props = {
  filters: Filters;
  page: number;
  onPage: (page: number) => void;
  selectedId: string | null;
};

export function EntryList({ filters, page, onPage, selectedId }: Props) {
  const { data, isPending, error } = useEntries(filters, page);

  if (isPending) return <p className="px-4 py-6 text-sm text-slate-500">加载中…</p>;
  if (error) {
    return (
      <p className="px-4 py-6 text-sm text-red-600">
        读取失败：{String(error.message)}（Qdrant 起了吗？`make up`）
      </p>
    );
  }
  if (!data || data.entries.length === 0) {
    return <p className="px-4 py-6 text-sm text-slate-500">没有符合条件的条目。</p>;
  }

  return (
    <>
      <ul data-testid="entry-list" className="flex-1 overflow-auto">
        {data.entries.map((entry) => (
          <li
            key={entry.id}
            className={`cursor-pointer border-b border-slate-100 px-4 py-2 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-700 ${
              entry.id === selectedId ? "bg-blue-50 dark:bg-slate-700" : ""
            }`}
            onClick={() => {
              window.location.hash = `#/entry/${entry.id}`;
            }}
          >
            <div className="text-xs text-slate-500 dark:text-slate-400">
              {entry.date} · {entry.type} · {entry.source}
              {entry.edited && <span className="ml-2 text-amber-600">已编辑</span>}
              {entry.deleted_at && <span className="ml-2 text-red-600">已删除</span>}
            </div>
            <div className="mt-0.5 truncate text-sm">{entry.text}</div>
          </li>
        ))}
      </ul>
      <div className="flex items-center gap-3 border-t border-slate-200 px-4 py-2 text-xs text-slate-500 dark:border-slate-700">
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
        <span>
          第 {data.page} 页 · 共 {data.total} 条（每页 {data.page_size}）
        </span>
      </div>
    </>
  );
}
