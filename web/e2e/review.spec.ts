import { expect, test } from "@playwright/test";

/**
 * AC-018~AC-021 的 8 步验收（T119）。
 *
 * **自带探针数据**：直接往 Qdrant upsert 一条测试点，跑完删掉 —— 全程不碰真库里的
 * 条目（软删可恢复也不该拿来当"反正能恢复"的借口）。"重放后人工值仍在"那一步由
 * 后端集成测试（tests/integration/test_entries_replay.py）覆盖，这里不重复跑 sync。
 */
const QDRANT = "http://127.0.0.1:6333";
const COLLECTION = "learning_memory";
const PROBE_ID = "019c0000-0000-7000-8000-00000000e2e2";
const PROBE_TEXT = "e2e 探针条目（Playwright 自己 upsert 的，跑完就删）";
const EDITED_TEXT = "e2e 探针条目 —— 已被人工改过";

test.beforeAll(async ({ request }) => {
  const response = await request.put(
    `${QDRANT}/collections/${COLLECTION}/points?wait=true`,
    {
      data: {
        points: [
          {
            id: PROBE_ID,
            vector: Array.from({ length: 2048 }, () => 0),
            payload: {
              text: PROBE_TEXT,
              date: "2099-01-01", // 未来日期 → 默认列表里排第一，便于定位
              type: "progress",
              tags: ["e2e"],
              source: "claude_code",
              project: "first-rag",
              created_at: new Date().toISOString(),
              source_refs: ["e2e-probe"],
              distill_version: "e2e+probe@00000000",
              related: [],
            },
          },
        ],
      },
    },
  );
  expect(response.ok(), `探针 upsert 失败：${response.status()}`).toBeTruthy();
});

test.afterAll(async ({ request }) => {
  const response = await request.post(
    `${QDRANT}/collections/${COLLECTION}/points/delete?wait=true`,
    { data: { points: [PROBE_ID] } },
  );
  expect(response.ok(), `探针清理失败（请手工删 ${PROBE_ID}）：${response.status()}`).toBeTruthy();
});

test("8 步验收：筛选 → 详情 → 编辑 → 已编辑标记 → 删除 → 显示已删除 → 恢复", async ({
  page,
}) => {
  // ① 打开页面（AC-018）
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /first-rag/ })).toBeVisible();

  // ② 按项目筛选后，探针条目在列表里（AC-018 的过滤面）
  await page.getByPlaceholder("first-rag").fill("first-rag");
  await page.getByRole("button", { name: "查询" }).click();
  const list = page.getByTestId("entry-list"); // 断言一律**限定在列表内**：详情面板会一直留着当前条目
  // 详情面板同理要限定：toast 也是 button，而 `getByRole(name)` 默认是**子串**匹配 ——
  // 曾因此报"恢复"命中两个元素（真按钮 + toast「已软删除（可恢复）」）。所以加 exact。
  const detail = page.locator("aside");
  const row = list.getByText(PROBE_TEXT, { exact: false }).first();
  await expect(row).toBeVisible();

  // ③ 点开详情：看到正文、标签、溯源（AC-018 的详情面）
  await row.click();
  await expect(page.getByRole("heading", { name: /2099-01-01/ })).toBeVisible();
  await expect(page.getByText("e2e", { exact: false }).first()).toBeVisible();

  // ④ 编辑正文并保存（AC-019 的界面面；ID 不变与重放不覆盖由后端测试覆盖）
  await detail.locator("textarea").fill(EDITED_TEXT);
  await detail.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("已保存", { exact: false })).toBeVisible();
  await expect(list.getByText(EDITED_TEXT, { exact: false }).first()).toBeVisible();
  await expect(list.getByText("已编辑", { exact: false }).first()).toBeVisible();
  // 原文仍可查（AC-021）。**必须 exact**：页脚那句"关联边基于原始蒸馏正文计算…"也含这个词。
  await expect(page.getByText("原始蒸馏正文", { exact: true })).toBeVisible();

  // ⑤ 删除（软删，可恢复）——prompt 里留空原因
  page.once("dialog", (dialog) => dialog.accept(""));
  await detail.getByRole("button", { name: "删除", exact: true }).click();
  await expect(page.getByText("已软删除", { exact: false })).toBeVisible();

  // ⑥ 默认列表里看不到它了（AC-020 前半）
  await expect(list.getByText(EDITED_TEXT, { exact: false })).toHaveCount(0);

  // ⑦ 勾"显示已删除"能看到（恢复入口）
  await page.getByLabel("显示已删除").check();
  await page.getByRole("button", { name: "查询" }).click();
  const deletedRow = list.getByText(EDITED_TEXT, { exact: false }).first();
  await expect(deletedRow).toBeVisible();
  await expect(list.getByText("已删除", { exact: false }).first()).toBeVisible();

  // ⑧ 恢复（AC-020 后半）
  // 不用再点一次行：详情面板从头到尾就停在探针这条上（`location.hash` 没变，点了也不会
  // 触发 hashchange）。直接点"恢复"——按钮在删除后由详情面板渲染出来。
  await detail.getByRole("button", { name: "恢复", exact: true }).click();
  await expect(page.getByText("已恢复", { exact: false })).toBeVisible();
  await page.getByLabel("显示已删除").uncheck();
  await page.getByRole("button", { name: "查询" }).click();
  await expect(list.getByText(EDITED_TEXT, { exact: false }).first()).toBeVisible();
});
