import { expectTypeOf, test } from "vitest";

import type {
  EntryDetailView,
  EntryList,
  EntryView,
  TypesView,
} from "../src/api/client";
import type { components } from "../src/api/schema";

/**
 * 类型层面的判据：前端用的视图类型必须**就是** openapi 快照里那个（T129）。
 *
 * 这条断言在运行时是空操作，真正的检查发生在 `pnpm typecheck` —— 所以它不是
 * "测了个寂寞"：T129 之前 client.ts 里的视图类型是手抄的一份，和后端各漂各的，
 * 而漂移不报错、只在运行时以 undefined 露出来。谁再手抄一份形状不同的，tsc 立刻红。
 */
test("视图类型就是 openapi 快照里那些，不是手抄的第二份", () => {
  expectTypeOf<EntryView>().toEqualTypeOf<components["schemas"]["EntryView"]>();
  expectTypeOf<EntryDetailView>().toEqualTypeOf<
    components["schemas"]["EntryDetailView"]
  >();
  expectTypeOf<EntryList>().toEqualTypeOf<components["schemas"]["EntryListView"]>();
  expectTypeOf<TypesView>().toEqualTypeOf<components["schemas"]["TypesView"]>();
});
