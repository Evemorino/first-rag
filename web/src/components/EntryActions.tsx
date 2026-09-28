import { describeWriteError, type EntryDetailView } from "../api/client";
import { useDeleteEntry, useRestoreEntry } from "../queries/mutations";
import { useToasts } from "../store/toast";

const BUTTON =
  "rounded border border-slate-300 px-3 py-1 text-sm disabled:opacity-40 " +
  "dark:border-slate-600";

/**
 * 软删 / 恢复（FR-029 / ADR-19）。
 *
 * 删除前用 `window.prompt` 收原因（可留空；取消则整件事中止）—— 它不是最漂亮的
 * 交互，换成模态框是 T125 的事；这里只保证"取消不会误删"。
 *
 * 两个按钮各自只看自己的 pending 状态：并发的第二笔写会被服务端的 `rev` 挡下
 * （409），界面会提示"已被别处改动，已刷新" —— 那比把所有按钮一起冻住更诚实，
 * 也更省状态（合并 busy 需要让两个组件共享一份状态）。
 */
export function EntryActions({ view }: { view: EntryDetailView }) {
  const pushToast = useToasts((state) => state.push);
  const remove = useDeleteEntry();
  const restore = useRestoreEntry();

  const onRestore = () => {
    restore.mutate(
      { id: view.id, rev: view.rev },
      {
        onSuccess: () => pushToast("已恢复"),
        onError: (error) => pushToast(describeWriteError(error), "error"),
      },
    );
  };

  const onDelete = () => {
    const reason = window.prompt("删除原因（可留空）：", "") ?? null;
    if (reason === null) return;                       // 取消 → 什么都不做
    remove.mutate(
      { id: view.id, rev: view.rev, reason },
      {
        onSuccess: () => pushToast("已软删除（可恢复）"),
        onError: (error) => pushToast(describeWriteError(error), "error"),
      },
    );
  };

  return (
    <div className="flex items-center gap-2">
      {view.deleted_at ? (
        <button className={BUTTON} disabled={restore.isPending} type="button"
          onClick={onRestore}>
          恢复
        </button>
      ) : (
        <button className={`${BUTTON} text-red-700`} disabled={remove.isPending}
          type="button" onClick={onDelete}>
          删除
        </button>
      )}
      <span className="text-xs text-slate-500">
        {remove.isPending && "删除中…"}
        {restore.isPending && "恢复中…"}
      </span>
    </div>
  );
}
