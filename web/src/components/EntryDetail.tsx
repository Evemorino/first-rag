import { useEntry } from "../queries/entries";
import { EntryActions } from "./EntryActions";
import { EntryEditor } from "./EntryEditor";
import { EntryMeta } from "./EntryMeta";

function Placeholder({ children, tone = "muted" }: {
  children: string;
  tone?: "muted" | "error";
}) {
  const color = tone === "error" ? "text-red-600" : "text-slate-500";
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
  if (isPending) return <Placeholder>加载中…</Placeholder>;
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
      <p className="whitespace-pre-wrap rounded border border-slate-200 bg-white p-3 text-sm dark:border-slate-700 dark:bg-slate-800">
        {data.text}
      </p>
      {data.edited && (
        <details className="text-sm">
          <summary className="cursor-pointer text-xs text-slate-500">原始蒸馏正文</summary>
          <p className="mt-1 whitespace-pre-wrap rounded border border-slate-200 p-3 text-slate-600 dark:border-slate-700 dark:text-slate-300">
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
