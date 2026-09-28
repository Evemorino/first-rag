import type { EntryDetailView } from "../api/client";

function Row({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null;
  return (
    <div className="flex gap-3 text-sm">
      <dt className="w-16 shrink-0 text-xs text-slate-500">{label}</dt>
      <dd className="min-w-0 break-words">{value}</dd>
    </div>
  );
}

/**
 * 元信息与留痕（FR-027）：来源、标签、溯源、关联边、编辑/删除痕迹、版本。
 *
 * 从 `EntryDetail` 拆出来的（T124）：那个组件一条函数 187 行，而"渲染一张表"和
 * "管三个写操作的交互"是两件事，混在一起既读不动也测不动。
 */
export function EntryMeta({ view }: { view: EntryDetailView }) {
  return (
    <>
      <dl className="space-y-1">
        <Row label="项目" value={view.project} />
        <Row label="来源" value={view.source} />
        <Row label="标签" value={(view.tags ?? []).join("、")} />
        <Row label="创建" value={view.created_at} />
        <Row label="溯源" value={view.source_refs.join("、")} />
        <Row
          label="关联边"
          value={
            view.related.length
              ? view.related.map((id) => id.slice(0, 8)).join("、")
              : undefined
          }
        />
        <Row label="编辑于" value={view.edited_at} />
        <Row label="删除于" value={view.deleted_at} />
        <Row label="原因" value={view.deleted_reason} />
        <Row label="版本" value={String(view.rev)} />
      </dl>
      <p className="text-xs text-slate-500">
        关联边基于原始蒸馏正文计算，不随人工编辑变化。
      </p>
    </>
  );
}
