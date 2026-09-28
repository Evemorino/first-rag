import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api, type EditPayload, type EntryView } from "../api/client";

/** 写能力（FR-028 / FR-029）。乐观更新：先改本地缓存，失败再回滚；成功后失效重取。 */
function useWrite<TArgs>(mutate: (args: TArgs) => Promise<EntryView>, id: (args: TArgs) => string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: mutate,
    onMutate: async (args: TArgs) => {
      const entryId = id(args);
      await queryClient.cancelQueries({ queryKey: ["entry", entryId] });
      const previous = queryClient.getQueryData<EntryView>(["entry", entryId]);
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
      queryClient.setQueryData(["entry", id(args)], view);
      queryClient.invalidateQueries({ queryKey: ["entries"] });
    },
  });
}

export function useEditEntry() {
  return useWrite<{ id: string; payload: EditPayload }>(
    ({ id, payload }) => api.edit(id, payload),
    ({ id }) => id,
  );
}

export function useDeleteEntry() {
  return useWrite<{ id: string; rev: number; reason?: string }>(
    ({ id, rev, reason }) => api.remove(id, { rev, reason }),
    ({ id }) => id,
  );
}

export function useRestoreEntry() {
  return useWrite<{ id: string; rev: number }>(
    ({ id, rev }) => api.restore(id, { rev }),
    ({ id }) => id,
  );
}
