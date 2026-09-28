import { type ChangeEvent, useState } from "react";

import { useTypes } from "../queries/entries";
import { emptyFilters, type Filters, useUi } from "../store/ui";

const CONTROL =
  "rounded border border-slate-300 px-2 py-1 text-sm text-slate-900 " +
  "dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100";

type TextFilterProps = {
  label: string;
  placeholder: string;
  value: string;
  onChange: (value: string) => void;
};

function TextFilter({ label, placeholder, value, onChange }: TextFilterProps) {
  return (
    <label className="flex items-center gap-1 text-xs text-slate-500">
      {label}
      <input
        className={`w-28 ${CONTROL}`}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

/**
 * 筛选栏（FR-026 的过滤面）。
 *
 * 草稿态（draft）留在**这里**，不放在 App：输入框每敲一个字就改状态，而真正生效的
 * 筛选条件（Zustand 里的 `filters`）只在提交时才改 —— 两者分开是"边打边查"和
 * "按一下再查"的区别。草稿进组件内部之后 App 只剩布局。
 */
export function FilterForm() {
  const filters = useUi((state) => state.filters);
  const setFilters = useUi((state) => state.setFilters);
  const { data: typesData } = useTypes();
  const [draft, setDraft] = useState<Filters>(filters);

  const pickType = (event: ChangeEvent<HTMLSelectElement>) =>
    setDraft({ ...draft, type: event.target.value });

  return (
    <form
      className="flex flex-wrap items-center gap-2 border-b border-slate-200 px-4 py-3 dark:border-slate-700"
      onSubmit={(event) => {
        event.preventDefault();
        setFilters(draft);
      }}
    >
      <TextFilter label="起" placeholder="2026-09-01" value={draft.date_from}
        onChange={(date_from) => setDraft({ ...draft, date_from })} />
      <TextFilter label="止" placeholder="2026-09-30" value={draft.date_to}
        onChange={(date_to) => setDraft({ ...draft, date_to })} />
      <label className="flex items-center gap-1 text-xs text-slate-500">
        类型
        <select className={CONTROL} value={draft.type} onChange={pickType}>
          <option value="">全部</option>
          {(typesData?.types ?? []).map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <TextFilter label="项目" placeholder="first-rag" value={draft.project}
        onChange={(project) => setDraft({ ...draft, project })} />
      <TextFilter label="来源" placeholder="claude_code" value={draft.source}
        onChange={(source) => setDraft({ ...draft, source })} />
      <label className="flex items-center gap-1 text-xs text-slate-500">
        <input
          type="checkbox"
          checked={draft.include_deleted}
          onChange={(event) =>
            setDraft({ ...draft, include_deleted: event.target.checked })
          }
        />
        显示已删除
      </label>
      <button
        className="rounded border border-slate-300 px-3 py-1 text-sm hover:border-blue-500 hover:text-blue-600 dark:border-slate-600"
        type="submit"
      >
        查询
      </button>
      <button
        className="text-xs text-slate-500 underline"
        type="button"
        onClick={() => {
          setDraft(emptyFilters);
          setFilters(emptyFilters);
        }}
      >
        清空
      </button>
    </form>
  );
}
