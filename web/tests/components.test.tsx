import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import type { EntryView } from "../src/api/client";
import { buildEntriesQuery } from "../src/api/params";
import { EntryDetail } from "../src/components/EntryDetail";
import { EntryList } from "../src/components/EntryList";
import { emptyFilters } from "../src/store/ui";

function entry(overrides: Partial<EntryView> = {}): EntryView {
  return {
    id: "id-1",
    text: "正文",
    type: "progress",
    tags: ["t"],
    project: "first-rag",
    date: "2026-09-20",
    source: "claude_code",
    created_at: "2026-09-20T10:00:00+08:00",
    source_refs: ["s"],
    distill_version: "m@1",
    related: [],
    original_text: "原文",
    edited: false,
    edited_at: null,
    deleted_at: null,
    deleted_reason: null,
    rev: 0,
    ...overrides,
  };
}

/** 把数据直接seed进缓存，省掉打桩 fetch —— 组件测试只关心渲染分支。 */
function seededClient(list: EntryView[], detail?: EntryView) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(["types"], { types: ["progress", "error"] });
  client.setQueryData(["entries", buildEntriesQuery(emptyFilters, 1)], {
    total: list.length,
    page: 1,
    page_size: 50,
    entries: list,
  });
  if (detail) client.setQueryData(["entry", detail.id], detail);
  return client;
}

function renderWith(client: QueryClient, node: ReactNode) {
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

describe("EntryList", () => {
  it("给人工改过 / 已软删的条目打徽章（AC-019/AC-020 的可见信号）", () => {
    renderWith(
      seededClient([
        entry({ id: "a", text: "被改过的", edited: true, edited_at: "2026-09-28T12:00:00+00:00" }),
        entry({ id: "b", text: "被删掉的", deleted_at: "2026-09-28T13:00:00+00:00" }),
      ]),
      <EntryList filters={emptyFilters} page={1} onPage={() => {}} selectedId={null} />,
    );

    expect(screen.getByText("已编辑")).toBeTruthy();
    expect(screen.getByText("已删除")).toBeTruthy();
  });
});

describe("EntryDetail", () => {
  it("有覆写时给出可展开的原始蒸馏正文（AC-021）", () => {
    const detail = entry({ edited: true, text: "人工版", original_text: "原始版" });
    renderWith(seededClient([], detail), <EntryDetail entryId={detail.id} />);
    expect(screen.getByText("原始蒸馏正文", { exact: true })).toBeTruthy();
  });

  it("没覆写时不显示「原始蒸馏正文」（免得让人以为丢过东西）", () => {
    const detail = entry({ edited: false });
    renderWith(seededClient([], detail), <EntryDetail entryId={detail.id} />);
    expect(screen.queryByText("原始蒸馏正文", { exact: true })).toBeNull();
  });

  it("类型是配置内下拉（PRD FR-028：不许造出配置外的类型）", () => {
    const detail = entry({ type: "progress" });
    renderWith(seededClient([], detail), <EntryDetail entryId={detail.id} />);
    const options = screen.getAllByRole("option").map((option) => option.textContent);
    expect(options).toEqual(["progress", "error"]);
  });
});
