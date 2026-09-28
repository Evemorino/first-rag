import { describe, expect, it } from "vitest";

import { buildEntriesQuery, splitTags } from "../src/api/params";
import { payloadOf } from "../src/components/EntryEditor";
import type { EntryDetailView } from "../src/api/client";
import { emptyFilters } from "../src/store/ui";

describe("buildEntriesQuery", () => {
  it("空筛选只带页码", () => {
    expect(buildEntriesQuery(emptyFilters, 1)).toBe("page=1");
  });

  it("只把填过的字段放进查询串（两端空白算没填）", () => {
    const query = buildEntriesQuery(
      { ...emptyFilters, date_from: " 2026-09-01 ", type: "error", project: "   " },
      2,
    );
    expect(query).toBe("page=2&date_from=2026-09-01&type=error");
  });

  it("勾了显示已删除才带 include_deleted", () => {
    expect(buildEntriesQuery({ ...emptyFilters, include_deleted: true }, 1)).toBe(
      "page=1&include_deleted=true",
    );
  });
});

describe("splitTags", () => {
  it("按逗号拆、去两端空白、丢掉空段", () => {
    expect(splitTags(" a, b ,, c ")).toEqual(["a", "b", "c"]);
  });

  it("空输入给空数组（不是 ['']）", () => {
    expect(splitTags("  ,  ")).toEqual([]);
  });
});

describe("payloadOf", () => {
  const view = {
    id: "a",
    text: "正文",
    type: "progress",
    tags: ["t"],
    project: "p",
    rev: 3,
  } as EntryDetailView;

  it("草稿里的标签串变成数组，rev 带上（乐观并发的前提）", () => {
    expect(
      payloadOf(view, { text: "改过", type: "idea", tags: " a, b ", project: " q " }),
    ).toEqual({ rev: 3, text: "改过", type: "idea", tags: ["a", "b"], project: " q " });
  });
});
