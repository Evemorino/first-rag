import { useEntry } from "../queries/entries";
import { META, PANEL, PROSE } from "../ui";
import { EntryActions } from "./EntryActions";
import { EntryEditor } from "./EntryEditor";
import { EntryMeta } from "./EntryMeta";

/** 详情骨架：比"加载中…"更能说明"这里将会有一块正文 + 一张表"。 */
function Skeleton() {
  return (
    <div className="space-y-3" aria-hidden="true">
      <div className="h-4 w-40 animate-pulse rounded bg-slate-200 dark:bg-slate-700" />
      <div className="h-24 w-full animate-pulse rounded bg-slate-200 dark:bg-slate-700" />
      {[0, 1, 2].map((row) => (
        <div key={row} className="h-3 w-2/3 animate-pulse rounded bg-slate-200 dark:bg-slate-700" />
      ))}
    </div>
  );
}

function Placeholder({ children, tone = "muted" }: {
  children: string;
  tone?: "muted" | "error";
}) {
  const color = tone === "error" ? "text-red-600 dark:text-red-400" : META;
  return <p className={`text-sm ${color}`}>{children}</p>;
}

/**
 * 详情面板（FR-027）。
 *
 * 只做三件事：取数、三个分支（没选 / 加载中 / 失败）、把正文与几块内容拼起来。
 * 编辑表单与软删恢复各自拆成组件（`EntryEditor` / `EntryActions`）—— 它们各自持有
 * 自己的写状态与提示，混在一条 187 行的函数里既读不动也测不动（T124）。
 */
export function EntryDetail({ entryId }: { entryId: string | null }) {
  const { data, isPending, error } = useEntry(entryId);

  if (!entryId) return <Placeholder>从左侧选一条查看详情。</Placeholder>;
  if (isPending) return <Skeleton />;
  if (error || !data) {
    return (
      <Placeholder tone="error">{`读取失败：${String(error?.message ?? "")}`}</Placeholder>
    );
  }

  return (
    <article className="space-y-3">
      <h2 className="text-sm font-semibold">
        {data.date} · {data.type}
        {data.deleted_at && <span className="ml-2 text-red-600">已删除</span>}
      </h2>
      <p className={`${PROSE} ${PANEL} bg-white p-3 text-sm dark:bg-slate-800`}>
        {data.text}
      </p>
      {data.edited && (
        <details className="text-sm">
          <summary className={`cursor-pointer ${META}`}>原始蒸馏正文</summary>
          <p className={`mt-1 ${PROSE} ${PANEL} p-3 text-slate-600 dark:text-slate-300`}>
            {data.original_text}
          </p>
        </details>
      )}
      <EntryMeta view={data} />
      <EntryEditor view={data} />
      <EntryActions view={data} />
    </article>
  );
}
