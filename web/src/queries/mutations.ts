import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  api,
  type EditPayload,
  type EntryDetailView,
  type EntryView,
} from "../api/client";

/** 乐观写：把这次调用的参数直接映射到缓存里那份详情上。 */
type Optimistic<TArgs> = (previous: EntryDetailView, args: TArgs) => EntryDetailView;

/**
 * 写能力（FR-028 / FR-029）：先改本地缓存（乐观），失败回滚，成功后对账。
 *
 * 写的是**详情缓存**（`["entry", id]`）。列表那一侧（`["entries", …]`）不做乐观标记
 * —— 分页、总数、过滤都在同一份缓存里，猜错会看到幽灵行或页码错位；那边交给
 * `onSuccess` / `onError` 的失效重取，代价是晚一个往返。
 */
function useWrite<TArgs>(
  mutate: (args: TArgs) => Promise<EntryView>,
  id: (args: TArgs) => string,
  optimistic: Optimistic<TArgs>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: mutate,
    onMutate: async (args: TArgs) => {
      const entryId = id(args);
      await queryClient.cancelQueries({ queryKey: ["entry", entryId] });
      const previous = queryClient.getQueryData<EntryDetailView>(["entry", entryId]);
      // 缓存里没有这条详情就不硬造一条：没有"原值"就既无所谓乐观、也无所谓回滚。
      if (previous) {
        queryClient.setQueryData<EntryDetailView>(
          ["entry", entryId],
          optimistic(previous, args),
        );
      }
      return { entryId, previous };
    },
    onError: (_error, _args, context) => {
      // 回滚 + 让列表/详情重新对账（409 时正是靠这一步拿到最新版本）
      if (context?.previous) {
        queryClient.setQueryData(["entry", context.entryId], context.previous);
      }
      queryClient.invalidateQueries({ queryKey: ["entries"] });
      queryClient.invalidateQueries({ queryKey: ["entry"] });
    },
    onSuccess: (view, args) => {
      // **合并**而不是覆盖：写接口返回的是 EntryView，而详情缓存是 EntryDetailView
      // —— 前者没有同日邻居字段（那是详情独有的）。直接覆盖会把 prev_id / next_id
      // 抹掉；这个差异是生成类型带来的，手写类型时看不出来（T129）。
      queryClient.setQueryData<EntryDetailView>(["entry", id(args)], (previous) =>
        previous ? { ...previous, ...view } : undefined,
      );
      queryClient.invalidateQueries({ queryKey: ["entries"] });
    },
  });
}

export function useEditEntry() {
  return useWrite<{ id: string; payload: EditPayload }>(
    ({ id, payload }) => api.edit(id, payload),
    ({ id }) => id,
    (previous, { payload }) => ({
      ...previous,
      // 只预测"用户看得见、服务端一定会照做"的字段。`rev` / `edited_at` /
      // `original_text` 是服务端算出来的，不猜 —— 猜错就是界面先跳到一个错的版本号，
      // 再被响应改回来：两次跳变比晚一个往返更难受。
      text: payload.text ?? previous.text,
      type: payload.type ?? previous.type,
      tags: payload.tags ?? previous.tags,
      project: payload.project ?? previous.project,
      edited: true,          // 有覆写 → "已编辑"徽章与原始正文折叠块都该出现
    }),
  );
}

export function useDeleteEntry() {
  return useWrite<{ id: string; rev: number; reason?: string }>(
    ({ id, rev, reason }) => api.remove(id, { rev, reason }),
    ({ id }) => id,
    (previous, { reason }) => ({
      ...previous,
      // 时间戳只是让"已删除"立刻可见的占位，响应回来就被服务端的值覆盖。
      deleted_at: new Date().toISOString(),
      deleted_reason: reason?.trim() ? reason : previous.deleted_reason,
    }),
  );
}

export function useRestoreEntry() {
  return useWrite<{ id: string; rev: number }>(
    ({ id, rev }) => api.restore(id, { rev }),
    ({ id }) => id,
    (previous) => ({ ...previous, deleted_at: null, deleted_reason: null }),
  );
}
