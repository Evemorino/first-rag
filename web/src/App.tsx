import { useEffect, useState } from "react";

import { EntryDetail } from "./components/EntryDetail";
import { EntryList } from "./components/EntryList";
import { FilterForm } from "./components/FilterForm";
import { Shortcuts } from "./components/Shortcuts";
import { ThemeToggle } from "./components/ThemeToggle";
import { Toasts } from "./components/Toasts";
import { readHashId } from "./hash-route";
import { useUi } from "./store/ui";
import { watchTheme } from "./store/theme";

/** 订阅哈希路由：`#/entry/<id>`（解析与拼装都在 hash-route.ts，三处共用一份）。 */
function useHashEntryId(): string | null {
  const [id, setId] = useState<string | null>(readHashId);
  useEffect(() => {
    const onChange = () => setId(readHashId());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return id;
}

/**
 * 布局壳：左列表、右详情、顶部标题与主题开关、底部提示。

 * 筛选栏的草稿态与三个写操作的状态都各自下沉到组件里（`FilterForm` /
 * `EntryEditor` / `EntryActions`）—— App 只剩布局、路由与全局副作用（主题订阅、
 * 快捷键监听），不然它会一路涨到 91 行并继续长（T124 的由来）。
 */
export function App() {
  const page = useUi((state) => state.page);
  const filters = useUi((state) => state.filters);
  const setPage = useUi((state) => state.setPage);
  const entryId = useHashEntryId();

  useEffect(() => watchTheme(), []);   // 启动同步 + 跟随系统变化

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 dark:bg-slate-900 dark:text-slate-100">
      <Shortcuts />
      <header className="flex items-center gap-3 border-b border-slate-200 bg-white px-5 py-3 dark:border-slate-700 dark:bg-slate-800">
        <div>
          <h1 className="text-base font-semibold">first-rag · 条目审阅</h1>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            浏览、筛选与人工修正已入库条目
          </p>
        </div>
        <div className="ml-auto">
          <ThemeToggle />
        </div>
      </header>
      <div className="flex h-[calc(100vh-70px)]">
        <section className="flex min-w-0 flex-1 flex-col border-r border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-800">
          <FilterForm />
          <EntryList
            filters={filters}
            page={page}
            onPage={setPage}
            selectedId={entryId}
          />
        </section>
        <aside className="w-[38%] overflow-auto px-5 py-4">
          <EntryDetail entryId={entryId} />
        </aside>
      </div>
      <Toasts />
    </div>
  );
}
