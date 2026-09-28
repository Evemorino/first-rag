/** 后端 JSON 端点的最小封装（只读面 + 写面）。 */

import type { components } from "./schema";

/** 写接口要求这个头（后端 require_local 的第二道闸，缺了就是 403）。 */
const UI_HEADER = "first-rag-ui";

type Schemas = components["schemas"];

/**
 * 视图类型**从后端 openapi.json 生成**（`make ui-types` → `schema.d.ts`，快照入库，
 * 与后端漂移时 CI 会红）。手写的那份在 T129 删掉了：手抄的类型和后端各漂各的，
 * 而漂移不会报错，只在运行时以"某个字段突然是 undefined"的形式露出来。
 *
 * 注意 `text` / `type` / `tags` 在生成类型里确实是**可空**的（`effective()` 取的是
 * payload 原值）—— 手写那份写成 `string` 是句假话，真遇到空值只会渲染出空白。
 */
export type EntryView = Schemas["EntryView"];
export type EntryDetailView = Schemas["EntryDetailView"];
export type EntryList = Schemas["EntryListView"];
export type TypesView = Schemas["TypesView"];
export type EditPayload = Schemas["EditBody"];
export type DeletePayload = Schemas["DeleteBody"];
export type RestorePayload = Schemas["RevBody"];

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`GET ${url} → ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

/** 写接口的错误：带上 HTTP 状态，界面据此区分 409（版本冲突）与 502（Ark/Qdrant 不可用）。 */
export class WriteError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "WriteError";
    this.status = status;
  }
}

async function sendJson<T>(method: string, url: string, body: unknown): Promise<T> {
  const response = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json", "X-Requested-With": UI_HEADER },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const detail: { detail?: string } = await response.json().catch(() => ({}));
    throw new WriteError(response.status, detail.detail ?? response.statusText);
  }
  return (await response.json()) as T;
}

export const api = {
  types: () => getJson<TypesView>("/types"),
  entries: (query: string) => getJson<EntryList>(`/entries?${query}`),
  entry: (id: string) =>
    getJson<EntryDetailView>(`/entries/${encodeURIComponent(id)}`),
  edit: (id: string, payload: EditPayload) =>
    sendJson<EntryView>("PATCH", `/entries/${encodeURIComponent(id)}`, payload),
  remove: (id: string, payload: DeletePayload) =>
    sendJson<EntryView>("POST", `/entries/${encodeURIComponent(id)}/delete`, payload),
  restore: (id: string, payload: RestorePayload) =>
    sendJson<EntryView>("POST", `/entries/${encodeURIComponent(id)}/restore`, payload),
};

/** 把写接口的失败翻译成人话 —— 409 与 502 的处置完全不同，不能混成一句"保存失败"。 */
export function describeWriteError(error: unknown): string {
  if (error instanceof WriteError) {
    if (error.status === 409) {
      return "这条已被别处改动，列表已刷新到最新版本，请重看后再改。";
    }
    if (error.status === 502) {
      return `保存失败：${error.message}（Ark 或 Qdrant 没起来？）`;
    }
    return `保存失败：${error.message}`;
  }
  return `保存失败：${String(error)}`;
}
