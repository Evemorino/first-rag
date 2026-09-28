/** 后端 JSON 端点的最小封装（只读面）。写面（PATCH/POST）在片 2 加。 */

/** 写接口要求这个头（后端 require_local 的第二道闸，缺了就是 403）。 */
const UI_HEADER = "first-rag-ui";

export type EntryView = {
  id: string;
  text: string;
  type: string;
  tags: string[];
  project: string | null;
  date: string;
  source: string;
  created_at: string | null;
  source_refs: string[];
  distill_version: string | null;
  related: string[];
  original_text: string;
  edited: boolean;
  edited_at: string | null;
  deleted_at: string | null;
  deleted_reason: string | null;
  rev: number;
  prev_id?: string | null;
  next_id?: string | null;
};

export type EntryList = {
  total: number;
  page: number;
  page_size: number;
  entries: EntryView[];
};

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

export type EditPayload = {
  rev: number;
  text?: string;
  type?: string;
  tags?: string[];
  project?: string;
};

export const api = {
  types: () => getJson<{ types: string[] }>("/types"),
  entries: (query: string) => getJson<EntryList>(`/entries?${query}`),
  entry: (id: string) => getJson<EntryView>(`/entries/${encodeURIComponent(id)}`),
  edit: (id: string, payload: EditPayload) =>
    sendJson<EntryView>("PATCH", `/entries/${encodeURIComponent(id)}`, payload),
  remove: (id: string, payload: { rev: number; reason?: string }) =>
    sendJson<EntryView>("POST", `/entries/${encodeURIComponent(id)}/delete`, payload),
  restore: (id: string, payload: { rev: number }) =>
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
