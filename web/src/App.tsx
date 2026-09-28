import { useEffect, useState } from "react";

import { EntryDetail } from "./components/EntryDetail";
import { EntryList } from "./components/EntryList";
import { Toasts } from "./components/Toasts";
import { useTypes } from "./queries/entries";
import { emptyFilters, type Filters, useUi } from "./store/ui";

function readHash(): string | null {
  const match = window.location.hash.match(/^#\/entry\/(.+)$/);
  return match ? decodeURIComponent(match[1]) : null;
}

function useHashEntryId(): string | null {
  const [id, setId] = useState<string | null>(readHash);
  useEffect(() => {
    const onChange = () => setId(readHash());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return id;
}

export function App() {
  const { filters, page, setFilters, setPage } = useUi();
  const { data: typesData } = useTypes();
  const [draft, setDraft] = useState<Filters>(filters);
  const entryId = useHashEntryId();

  const field = (key: keyof Filters, label: string, placeholder: string) => (
    <label className="flex items-center gap-1 text-xs text-slate-500">
      {label}
      <input
        className="w-28 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
        value={String(draft[key])}
        placeholder={placeholder}
        onChange={(event) => setDraft({ ...draft, [key]: event.target.value })}
      />
    </label>
  );

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 dark:bg-slate-900 dark:text-slate-100">
      <header className="border-b border-slate-200 bg-white px-5 py-3 dark:border-slate-700 dark:bg-slate-800">
        <h1 className="text-base font-semibold">first-rag · 条目审阅</h1>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          浏览与筛选已入库条目（片 1：只读；编辑与软删在片 2）
        </p>
      </header>
      <div className="flex h-[calc(100vh-70px)]">
        <section className="flex min-w-0 flex-1 flex-col border-r border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-800">
          <form
            className="flex flex-wrap items-center gap-2 border-b border-slate-200 px-4 py-3 dark:border-slate-700"
            onSubmit={(event) => {
              event.preventDefault();
              setFilters(draft);
            }}
          >
            {field("date_from", "起", "2026-09-01")}
            {field("date_to", "止", "2026-09-30")}
            <label className="flex items-center gap-1 text-xs text-slate-500">
              类型
              <select
                className="rounded border border-slate-300 px-2 py-1 text-sm text-slate-900 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
                value={draft.type}
                onChange={(event) => setDraft({ ...draft, type: event.target.value })}
              >
                <option value="">全部</option>
                {(typesData?.types ?? []).map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            {field("project", "项目", "first-rag")}
            {field("source", "来源", "claude_code")}
            <label className="flex items-center gap-1 text-xs text-slate-500">
              <input
                type="checkbox"
                checked={draft.include_deleted}
                onChange={(event) =>
                  setDraft({ ...draft, include_deleted: event.target.checked })
                }
              />
              显示已删除
            </label>
            <button
              className="rounded border border-slate-300 px-3 py-1 text-sm hover:border-blue-500 hover:text-blue-600 dark:border-slate-600"
              type="submit"
            >
              查询
            </button>
            <button
              className="text-xs text-slate-500 underline"
              type="button"
              onClick={() => {
                setDraft(emptyFilters);
                setFilters(emptyFilters);
              }}
            >
              清空
            </button>
          </form>
          <EntryList filters={filters} page={page} onPage={setPage} selectedId={entryId} />
        </section>
        <aside className="w-[38%] overflow-auto px-5 py-4">
          <EntryDetail entryId={entryId} />
        </aside>
      </div>
      <Toasts />
    </div>
  );
}
