import { useEffect, useState } from "react";

import { describeWriteError } from "../api/client";
import { useEditEntry, useDeleteEntry, useRestoreEntry } from "../queries/mutations";
import { useEntry, useTypes } from "../queries/entries";
import { useToasts } from "../store/toast";

function Row({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null;
  return (
    <div className="flex gap-3 text-sm">
      <dt className="w-16 shrink-0 text-xs text-slate-500">{label}</dt>
      <dd className="min-w-0 break-words">{value}</dd>
    </div>
  );
}

export function EntryDetail({ entryId }: { entryId: string | null }) {
  const { data, isPending, error } = useEntry(entryId);
  const { data: typesData } = useTypes();
  const pushToast = useToasts((state) => state.push);
  const edit = useEditEntry();
  const remove = useDeleteEntry();
  const restore = useRestoreEntry();
  const [draft, setDraft] = useState({ text: "", type: "", tags: "", project: "" });

  // 换条目时把草稿重置为该条的当前值（避免把 A 的编辑带到 B 上）。
  useEffect(() => {
    if (data) {
      setDraft({
        text: data.text,
        type: data.type,
        tags: data.tags.join(", "),
        project: data.project ?? "",
      });
    }
  }, [data?.id, data?.rev]);

  if (!entryId) return <p className="text-sm text-slate-500">从左侧选一条查看详情。</p>;
  if (isPending) return <p className="text-sm text-slate-500">加载中…</p>;
  if (error || !data) {
    return <p className="text-sm text-red-600">读取失败：{String(error?.message ?? "")}</p>;
  }

  const busy = edit.isPending || remove.isPending || restore.isPending;

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
      <dl className="space-y-1">
        <Row label="项目" value={data.project} />
        <Row label="来源" value={data.source} />
        <Row label="标签" value={data.tags.join("、")} />
        <Row label="创建" value={data.created_at} />
        <Row label="溯源" value={data.source_refs.join("、")} />
        <Row
          label="关联边"
          value={
            data.related.length
              ? data.related.map((id) => id.slice(0, 8)).join("、")
              : undefined
          }
        />
        <Row label="编辑于" value={data.edited_at} />
        <Row label="删除于" value={data.deleted_at} />
        <Row label="原因" value={data.deleted_reason} />
        <Row label="版本" value={String(data.rev)} />
      </dl>
      <p className="text-xs text-slate-500">
        关联边基于原始蒸馏正文计算，不随人工编辑变化。
      </p>
      <form
        className="space-y-2 border-t border-slate-200 pt-3 dark:border-slate-700"
        onSubmit={(event) => {
          event.preventDefault();
          edit.mutate(
            {
              id: data.id,
              payload: {
                rev: data.rev,
                text: draft.text,
                type: draft.type,
                tags: draft.tags
                  .split(",")
                  .map((tag) => tag.trim())
                  .filter(Boolean),
                project: draft.project,
              },
            },
            {
              onSuccess: () => pushToast("已保存（人工覆写生效）"),
              onError: (writeError) => pushToast(describeWriteError(writeError), "error"),
            },
          );
        }}
      >
        <textarea
          className="w-full rounded border border-slate-300 p-2 text-sm dark:border-slate-600 dark:bg-slate-900"
          rows={4}
          value={draft.text}
          onChange={(event) => setDraft({ ...draft, text: event.target.value })}
        />
        <div className="flex gap-2">
          {/* 类型是**配置内下拉**（PRD FR-028 / NFR-011：人工编辑不许造出配置外的类型）。
              当前值万一不在配置里（历史数据），保留成一个显式标注的选项，让人看得见。 */}
          <select
            className="rounded border border-slate-300 px-2 py-1 text-sm dark:border-slate-600 dark:bg-slate-900"
            value={draft.type}
            onChange={(event) => setDraft({ ...draft, type: event.target.value })}
          >
            {(typesData?.types ?? []).map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
            {draft.type && !(typesData?.types ?? []).includes(draft.type) && (
              <option value={draft.type}>{draft.type}（配置外）</option>
            )}
          </select>
          <input
            className="flex-1 rounded border border-slate-300 px-2 py-1 text-sm dark:border-slate-600 dark:bg-slate-900"
            placeholder="标签，逗号分隔"
            value={draft.tags}
            onChange={(event) => setDraft({ ...draft, tags: event.target.value })}
          />
          <input
            className="w-32 rounded border border-slate-300 px-2 py-1 text-sm dark:border-slate-600 dark:bg-slate-900"
            placeholder="项目"
            value={draft.project}
            onChange={(event) => setDraft({ ...draft, project: event.target.value })}
          />
        </div>
        <div className="flex items-center gap-2">
          <button
            className="rounded border border-slate-300 px-3 py-1 text-sm hover:border-blue-500 hover:text-blue-600 disabled:opacity-40 dark:border-slate-600"
            disabled={busy}
            type="submit"
          >
            保存
          </button>
          {data.deleted_at ? (
            <button
              className="rounded border border-slate-300 px-3 py-1 text-sm disabled:opacity-40 dark:border-slate-600"
              disabled={busy}
              type="button"
              onClick={() =>
                restore.mutate(
                  { id: data.id, rev: data.rev },
                  {
                    onSuccess: () => pushToast("已恢复"),
                    onError: (writeError) => pushToast(describeWriteError(writeError), "error"),
                  },
                )
              }
            >
              恢复
            </button>
          ) : (
            <button
              className="rounded border border-slate-300 px-3 py-1 text-sm text-red-700 disabled:opacity-40 dark:border-slate-600"
              disabled={busy}
              type="button"
              onClick={() => {
                const reason = window.prompt("删除原因（可留空）：", "") ?? null;
                if (reason === null) return;
                remove.mutate(
                  { id: data.id, rev: data.rev, reason },
                  {
                    onSuccess: () => pushToast("已软删除（可恢复）"),
                    onError: (writeError) => pushToast(describeWriteError(writeError), "error"),
                  },
                );
              }}
            >
              删除
            </button>
          )}
          <span className="text-xs text-slate-500">
            {edit.isPending && "保存中…"}
            {remove.isPending && "删除中…"}
            {restore.isPending && "恢复中…"}
          </span>
        </div>
      </form>
    </article>
  );
}
