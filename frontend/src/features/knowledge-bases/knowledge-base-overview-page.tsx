import { useOutletContext } from 'react-router-dom'
import type { KnowledgeBase } from '../../api/knowledge-bases'

export function KnowledgeBaseOverviewPage() {
  const { knowledgeBase } = useOutletContext<{ knowledgeBase: KnowledgeBase }>()
  return (
    <dl className="grid gap-[var(--space-4)] sm:grid-cols-2">
      <Item label="描述" value={knowledgeBase.description || '暂无描述'} />
      <Item label="状态" value={knowledgeBase.status} />
      <Item label="资源权限" value={knowledgeBase.permission} />
      <Item label="文档数量" value={String(knowledgeBase.documentCount)} />
      <Item label="默认检索模式" value={knowledgeBase.defaultQueryMode} />
      <Item label="部门 ID" value={knowledgeBase.departmentId} />
    </dl>
  )
}

function Item({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 p-[var(--space-4)]">
      <dt className="text-xs font-semibold text-[var(--color-text-secondary)]">{label}</dt>
      <dd className="mt-[var(--space-1)] break-words text-sm">{value}</dd>
    </div>
  )
}
