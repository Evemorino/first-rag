import { describe, expect, it } from "vitest";

import { buildEntriesQuery } from "../src/api/params";
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
