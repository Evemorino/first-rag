// 后缀必须是 .tsx：下面的 Provider 包装是 JSX（esbuild 只看后缀，不猜内容）。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EntryDetailView, EntryView } from "../src/api/client";
import {
  useDeleteEntry,
  useEditEntry,
  useRestoreEntry,
} from "../src/queries/mutations";

/**
 * 写操作的缓存行为（T130）。这些断言存在的原因很具体：`mutations.ts` 的文件头曾经
 * 写着"乐观更新"，而代码里只有回滚 —— 没有一行 `setQueryData` 写乐观值，也没有一条
 * 测试碰过 `onMutate` / `onError`（`rg "乐观|回滚|rollback|onError" web/tests/` 曾零命中）。
 * 所以这里把三件事钉在缓存上：**乐观值先出现**、**失败回到原值**、**成功后保留详情
 * 独有的邻居字段**。
 */
const DETAIL: EntryDetailView = {
  id: "a",
  text: "原正文",
  type: "progress",
  tags: ["t"],
  project: "first-rag",
  date: "2026-09-20",
  source: "claude_code",
  created_at: "2026-09-20T10:00:00+08:00",
  source_refs: ["s"],
  distill_version: "m@1",
  related: [],
  original_text: "原正文",
  edited: false,
  edited_at: null,
  deleted_at: null,
  deleted_reason: null,
  rev: 3,
  prev_id: "p1",
  next_id: "n1",
};

/** 写接口返回的是 **EntryView**：字段与详情一样，但**没有** prev_id / next_id。 */
const WRITE_RESPONSE: EntryView = {
  id: DETAIL.id,
  text: "服务端版",
  type: DETAIL.type,
  tags: DETAIL.tags,
  project: DETAIL.project,
  date: DETAIL.date,
  source: DETAIL.source,
  created_at: DETAIL.created_at,
  source_refs: DETAIL.source_refs,
  distill_version: DETAIL.distill_version,
  related: DETAIL.related,
  original_text: DETAIL.original_text,
  edited: true,
  edited_at: "2026-09-28T12:00:00+00:00",
  deleted_at: null,
  deleted_reason: null,
  rev: 4,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function setup() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  client.setQueryData<EntryDetailView>(["entry", "a"], DETAIL);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper, cached: () => client.getQueryData<EntryDetailView>(["entry", "a"]) };
}

/** 一个悬着的 fetch：用来观察"响应还没回来"那一刻的缓存。 */
function pendingFetch() {
  let release: (response: Response) => void = () => {};
  vi.stubGlobal("fetch", vi.fn(
    () => new Promise<Response>((resolve) => { release = resolve; }),
  ));
  return { release: (response: Response) => release(response) };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("乐观值必须先出现（响应还没回来）", () => {
  it("编辑：详情里已经是新正文", async () => {
    const { release } = pendingFetch();
    const { wrapper, cached } = setup();
    const { result } = renderHook(() => useEditEntry(), { wrapper });

    act(() => {
      result.current.mutate({ id: "a", payload: { rev: 3, text: "改过的" } });
    });

    await waitFor(() => expect(cached()?.text).toBe("改过的"));
    expect(cached()?.edited).toBe(true);
    // 服务端算的字段不预测：版本号仍是原值，等响应回来对账。
    expect(cached()?.rev).toBe(3);

    release(jsonResponse(WRITE_RESPONSE));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it("删除：立刻出现「已删除」标记", async () => {
    const { release } = pendingFetch();
    const { wrapper, cached } = setup();
    const { result } = renderHook(() => useDeleteEntry(), { wrapper });

    act(() => {
      result.current.mutate({ id: "a", rev: 3, reason: " 错了 " });
    });

    await waitFor(() => expect(cached()?.deleted_at).toBeTruthy());
    expect(cached()?.deleted_reason).toBe(" 错了 ");

    release(jsonResponse({ ...WRITE_RESPONSE, deleted_at: "2026-09-28T00:00:00+00:00" }));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it("恢复：立刻清掉「已删除」标记", async () => {
    const { release } = pendingFetch();
    const { client, wrapper, cached } = setup();
    client.setQueryData<EntryDetailView>(["entry", "a"], {
      ...DETAIL,
      deleted_at: "2026-09-28T00:00:00+00:00",
      deleted_reason: "错了",
    });
    const { result } = renderHook(() => useRestoreEntry(), { wrapper });

    act(() => {
      result.current.mutate({ id: "a", rev: 4 });
    });

    await waitFor(() => expect(cached()?.deleted_at).toBeNull());
    expect(cached()?.deleted_reason).toBeNull();

    release(jsonResponse(WRITE_RESPONSE));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });
});

describe("失败回滚与成功对账", () => {
  it("409 之后缓存回到原值", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      jsonResponse({ detail: "版本冲突" }, 409)));
    const { wrapper, cached } = setup();
    const { result } = renderHook(() => useEditEntry(), { wrapper });

    act(() => {
      result.current.mutate({ id: "a", payload: { rev: 1, text: "改过的" } });
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(cached()?.text).toBe("原正文");
    expect(cached()?.edited).toBe(false);
  });

  it("成功时以响应为准，并保留详情独有的邻居字段", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(WRITE_RESPONSE)));
    const { wrapper, cached } = setup();
    const { result } = renderHook(() => useEditEntry(), { wrapper });

    act(() => {
      result.current.mutate({ id: "a", payload: { rev: 3, text: "本地版" } });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(cached()?.text).toBe("服务端版");   // 不是乐观值
    expect(cached()?.rev).toBe(4);
    expect(cached()?.prev_id).toBe("p1");      // 没被"覆盖"抹掉
    expect(cached()?.next_id).toBe("n1");
  });

  it("缓存里没有这条详情时不硬造一条", async () => {
    const { release } = pendingFetch();
    const client = new QueryClient({
      defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useEditEntry(), { wrapper });

    act(() => {
      result.current.mutate({ id: "missing", payload: { rev: 0, text: "x" } });
    });

    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(client.getQueryData(["entry", "missing"])).toBeUndefined();

    release(jsonResponse({ ...WRITE_RESPONSE, id: "missing" }));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });
});
