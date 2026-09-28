import { useEffect, useState } from "react";

import {
  describeWriteError,
  type EditPayload,
  type EntryDetailView,
} from "../api/client";
import { splitTags } from "../api/params";
import { useEditEntry } from "../queries/mutations";
import { useTypes } from "../queries/entries";
import { useToasts } from "../store/toast";

type Draft = { text: string; type: string; tags: string; project: string };

const CONTROL =
  "rounded border border-slate-300 px-2 py-1 text-sm text-slate-900 " +
  "dark:border-slate-600 dark:bg-slate-900";

function draftOf(view: EntryDetailView): Draft {
  return {
    // `?? ""` / `?? []` 不是防御性冗余：生成类型里 text / type / tags 确实可空
    // （后端 `effective()` 取 payload 原值）。手写类型把它们写成必填，
    // 只是把这件事藏在编译期之外。
    text: view.text ?? "",
    type: view.type ?? "",
    tags: (view.tags ?? []).join(", "),
    project: view.project ?? "",
  };
}

/** 草稿 → 写接口的 payload（纯函数：标签拆分这类转换最容易悄悄写错）。 */
export function payloadOf(view: EntryDetailView, draft: Draft): EditPayload {
  return {
    rev: view.rev,
    text: draft.text,
    type: draft.type,
    tags: splitTags(draft.tags),
    project: draft.project,
  };
}

/**
 * 编辑表单（FR-028 / ADR-18）：覆写层 + 留痕，提交时带上 `rev` 做乐观并发。
 *
 * 草稿重置的判据是 `[view.id, view.rev]`：换条目要重置，**服务端版本变了也要重置**
 * —— 别处改过之后接口返回 409，界面会拉到最新版；那一刻草稿若还停在旧版本，
 * 用户就会把旧正文又存回去。
 */
export function EntryEditor({ view }: { view: EntryDetailView }) {
  const { data: typesData } = useTypes();
  const pushToast = useToasts((state) => state.push);
  const edit = useEditEntry();
  const [draft, setDraft] = useState<Draft>(() => draftOf(view));

  useEffect(() => {
    setDraft(draftOf(view));
  }, [view.id, view.rev]);

  const types = typesData?.types ?? [];
  const foreign = draft.type !== "" && !types.includes(draft.type);

  const save = () => {
    edit.mutate(
      { id: view.id, payload: payloadOf(view, draft) },
      {
        onSuccess: () => pushToast("已保存（人工覆写生效）"),
        onError: (error) => pushToast(describeWriteError(error), "error"),
      },
    );
  };

  return (
    <form
      className="space-y-2 border-t border-slate-200 pt-3 dark:border-slate-700"
      onSubmit={(event) => {
        event.preventDefault();
        save();
      }}
    >
      <textarea
        className={`w-full ${CONTROL}`}
        rows={4}
        value={draft.text}
        onChange={(event) => setDraft({ ...draft, text: event.target.value })}
      />
      <div className="flex gap-2">
        {/* 类型是**配置内下拉**（PRD FR-028 / NFR-011：人工编辑不许造出配置外的类型）。
            当前值万一不在配置里（历史数据），保留成一个显式标注的选项，让人看得见。 */}
        <select
          className={CONTROL}
          value={draft.type}
          onChange={(event) => setDraft({ ...draft, type: event.target.value })}
        >
          {types.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
          {foreign && <option value={draft.type}>{draft.type}（配置外）</option>}
        </select>
        <input
          className={`flex-1 ${CONTROL}`}
          placeholder="标签，逗号分隔"
          value={draft.tags}
          onChange={(event) => setDraft({ ...draft, tags: event.target.value })}
        />
        <input
          className={`w-32 ${CONTROL}`}
          placeholder="项目"
          value={draft.project}
          onChange={(event) => setDraft({ ...draft, project: event.target.value })}
        />
      </div>
      <div className="flex items-center gap-2">
        <button
          className="rounded border border-slate-300 px-3 py-1 text-sm hover:border-blue-500 hover:text-blue-600 disabled:opacity-40 dark:border-slate-600"
          disabled={edit.isPending}
          type="submit"
        >
          保存
        </button>
        <span className="text-xs text-slate-500">{edit.isPending && "保存中…"}</span>
      </div>
    </form>
  );
}
