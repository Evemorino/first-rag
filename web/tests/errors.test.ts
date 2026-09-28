import { describe, expect, it } from "vitest";

import { describeWriteError, WriteError } from "../src/api/client";

describe("describeWriteError", () => {
  it("409 说清是版本冲突，并给出下一步（刷新后再改）", () => {
    const message = describeWriteError(new WriteError(409, "条目已被改动（当前版本 2）"));
    expect(message).toContain("已被别处改动");
    expect(message).toContain("刷新");
  });

  it("502 提示 Ark/Qdrant 可能就是原因", () => {
    expect(describeWriteError(new WriteError(502, "保存失败：Ark timeout"))).toContain("Ark");
  });

  it("其他状态照原样带出来，不吞信息", () => {
    expect(describeWriteError(new WriteError(400, "正文不能为空"))).toContain("正文不能为空");
  });
});
