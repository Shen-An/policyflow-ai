import { Activity, Bug, Play, Plus } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Button } from '../../components/ui/button'
import { Alert } from '../../components/feedback/alert'
import { LoadingState } from '../../components/feedback/state-views'
import { useKnowledgeBasesQuery } from '../knowledge-bases/queries'
import {
  useCreateEvalCaseMutation,
  useCreateEvalRunMutation,
  useCreateRetrievalItemMutation,
  useEvalCasesQuery,
  useEvalRunQuery,
  useEvalRunsQuery,
  useRetrievalDebugMutation,
  useRetrievalItemsQuery,
} from './queries'

export function EvaluationPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const runId = searchParams.get('run_id') ?? ''
  return (
    <section>
      <h2 className="text-2xl font-semibold">评估中心</h2>
      <p className="mt-1 text-sm text-[var(--color-text-secondary)]">管理评估用例、运行历史和单次检索调试；skipped/disabled 不按 0 分展示。</p>
      <div className="mt-6 space-y-8">
        <DatasetSection />
        <RunSection selectedRunId={runId} onSelectRun={(id) => {
          const next = new URLSearchParams(searchParams)
          if (id) next.set('run_id', id)
          else next.delete('run_id')
          setSearchParams(next)
        }} />
        <RetrievalDebugSection />
      </div>
    </section>
  )
}

function DatasetSection() {
  const cases = useEvalCasesQuery()
  const items = useRetrievalItemsQuery()
  const createCase = useCreateEvalCaseMutation()
  const createItem = useCreateRetrievalItemMutation()
  const knowledgeBases = useKnowledgeBasesQuery()
  const [question, setQuestion] = useState('')
  const [category, setCategory] = useState('hr')
  const [keywords, setKeywords] = useState('')
  const [sourceTitles, setSourceTitles] = useState('')
  const [retrievalQuery, setRetrievalQuery] = useState('')
  const [selectedKb, setSelectedKb] = useState('')
  const [evalCaseId, setEvalCaseId] = useState('')
  const [relevantDocuments, setRelevantDocuments] = useState('')

  return <div className="rounded-xl border border-[var(--color-border)] bg-white p-5 shadow-sm"><h3 className="text-lg font-semibold">评估数据集</h3>
    <div className="mt-4 grid gap-6 xl:grid-cols-2">
      <form onSubmit={(event) => { event.preventDefault(); createCase.mutate({ question: question.trim(), category: category.trim(), expectedAnswerKeywords: splitCsv(keywords), expectedSourceDocuments: splitCsv(sourceTitles), shouldAnswer: true }, { onSuccess: () => { setQuestion(''); setKeywords(''); setSourceTitles('') } }) }} className="space-y-3 rounded-lg bg-slate-50 p-4">
        <h4 className="font-semibold">新增回答评估用例</h4>
        <Field label="问题" value={question} onChange={setQuestion} />
        <Field label="知识库分类代码" value={category} onChange={setCategory} />
        <Field label="期望关键词（逗号分隔）" value={keywords} onChange={setKeywords} />
        <Field label="期望来源标题（逗号分隔）" value={sourceTitles} onChange={setSourceTitles} />
        <Button type="submit" disabled={!question.trim() || !category.trim() || createCase.isPending}><Plus className="size-4" />创建用例</Button>
        {createCase.isError ? <Alert tone="danger">{createCase.error.message}</Alert> : null}
      </form>
      <form onSubmit={(event) => { event.preventDefault(); createItem.mutate({ evalCaseId: evalCaseId || undefined, query: retrievalQuery.trim(), knowledgeBaseIds: [selectedKb], relevantDocumentIds: splitCsv(relevantDocuments) }, { onSuccess: () => { setRetrievalQuery(''); setRelevantDocuments('') } }) }} className="space-y-3 rounded-lg bg-slate-50 p-4">
        <h4 className="font-semibold">新增检索评估用例</h4>
        <Field label="查询" value={retrievalQuery} onChange={setRetrievalQuery} />
        <label className="block text-sm font-semibold">知识库<select value={selectedKb} onChange={(event) => setSelectedKb(event.target.value)} className="mt-2 min-h-11 w-full rounded-md border border-[var(--color-border)] px-3 font-normal"><option value="">请选择</option>{knowledgeBases.data?.map((kb) => <option key={kb.id} value={kb.id}>{kb.name}</option>)}</select></label>
        <label className="block text-sm font-semibold">关联回答用例<select value={evalCaseId} onChange={(event) => setEvalCaseId(event.target.value)} className="mt-2 min-h-11 w-full rounded-md border border-[var(--color-border)] px-3 font-normal"><option value="">无</option>{cases.data?.map((item) => <option key={item.id} value={item.id}>{item.question}</option>)}</select></label>
        <Field label="相关文档 ID（逗号分隔，可空）" value={relevantDocuments} onChange={setRelevantDocuments} />
        <Button type="submit" disabled={!retrievalQuery.trim() || !selectedKb || createItem.isPending}><Plus className="size-4" />创建检索用例</Button>
        {createItem.isError ? <Alert tone="danger">{createItem.error.message}</Alert> : null}
      </form>
    </div>
    <div className="mt-5 grid gap-4 lg:grid-cols-2"><List title={`回答用例（${cases.data?.length ?? 0}）`} loading={cases.isPending} items={cases.data?.map((item) => `${item.enabled ? 'enabled' : 'disabled'} · ${item.category} · ${item.question}`) ?? []} /><List title={`检索用例（${items.data?.length ?? 0}）`} loading={items.isPending} items={items.data?.map((item) => `${item.enabled ? 'enabled' : 'disabled'} · ${item.query}`) ?? []} /></div>
  </div>
}

function RunSection({ selectedRunId, onSelectRun }: { selectedRunId: string; onSelectRun: (id: string) => void }) {
  const cases = useEvalCasesQuery()
  const retrievalItems = useRetrievalItemsQuery()
  const runs = useEvalRunsQuery(1, 20, '')
  const create = useCreateEvalRunMutation()
  const [name, setName] = useState('')
  const [caseIds, setCaseIds] = useState<string[]>([])
  const [itemIds, setItemIds] = useState<string[]>([])
  const [evalTypes, setEvalTypes] = useState<Array<'retrieval' | 'rag_answer' | 'ragas'>>(['retrieval'])

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    const run = await create.mutateAsync({ name: name.trim(), caseIds, retrievalItemIds: itemIds, evalTypes, queryMode: 'hybrid' })
    setName('')
    onSelectRun(run.id)
  }

  return <div className="rounded-xl border border-[var(--color-border)] bg-white p-5 shadow-sm"><h3 className="text-lg font-semibold">评估 Run</h3>
    <form onSubmit={submit} className="mt-4 grid gap-4 rounded-lg bg-slate-50 p-4 lg:grid-cols-4">
      <Field label="Run 名称" value={name} onChange={setName} />
      <CheckGroup title="评估类型" options={[['retrieval','检索'],['rag_answer','回答'],['ragas','RAGAS']]} selected={evalTypes} setSelected={(values) => setEvalTypes(values as typeof evalTypes)} />
      <CheckGroup title="回答用例" options={(cases.data ?? []).map((item) => [item.id, item.question])} selected={caseIds} setSelected={setCaseIds} />
      <CheckGroup title="检索用例" options={(retrievalItems.data ?? []).map((item) => [item.id, item.query])} selected={itemIds} setSelected={setItemIds} />
      <div className="lg:col-span-4"><Button type="submit" disabled={!name.trim() || create.isPending}><Play className="size-4" />启动评估</Button>{create.isError ? <Alert tone="danger" className="mt-2">{create.error.message}</Alert> : null}</div>
    </form>
    <div className="mt-5 overflow-x-auto"><table className="min-w-full text-left text-sm"><thead><tr>{['名称','状态','用例数','创建时间','操作'].map((x) => <th key={x} className="px-3 py-2">{x}</th>)}</tr></thead><tbody className="divide-y divide-[var(--color-border)]">{runs.data?.items.map((run) => <tr key={run.id}><td className="px-3 py-3 font-semibold">{run.name}</td><td className="px-3 py-3"><Status value={run.status} /></td><td className="px-3 py-3">{run.totalCases}</td><td className="px-3 py-3">{new Date(run.createdAt).toLocaleString('zh-CN')}</td><td className="px-3 py-3"><Button className="min-h-8 py-1 text-xs" onClick={() => onSelectRun(run.id)}>查看结果</Button></td></tr>)}</tbody></table></div>
    {selectedRunId ? <RunDetail id={selectedRunId} onClose={() => onSelectRun('')} /> : null}
  </div>
}

function RunDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const query = useEvalRunQuery(id)
  if (query.isPending) return <div className="mt-5"><LoadingState message="正在加载 Run…" minH="min-h-0" /></div>
  if (query.isError) return <Alert tone="danger" className="mt-5">{query.error.message}</Alert>
  const run = query.data
  return <div className="mt-6 rounded-lg border border-[var(--color-border)] p-4"><div className="flex items-start justify-between"><div><h4 className="font-semibold">{run.name}</h4><p className="mt-1 text-xs">Request ID：{run.requestId ?? '无'}</p></div><Button className="min-h-8 py-1 text-xs" onClick={onClose}>关闭</Button></div>
    <div className="mt-4 flex flex-wrap gap-3"><Status value={run.status} />{Object.entries(run.metrics).map(([key,value]) => <span key={key} className="rounded-md bg-slate-100 px-2 py-1 text-xs">{key}: {String(value)}</span>)}</div>
    {run.errorSummary ? <Alert tone="danger" className="mt-3">{run.errorSummary}</Alert> : null}
    <details className="mt-4 rounded-md border border-[var(--color-border)]"><summary className="cursor-pointer p-3 font-semibold">配置快照</summary><pre className="overflow-auto border-t p-3 text-xs">{JSON.stringify(run.configSnapshot, null, 2)}</pre></details>
    <div className="mt-4 space-y-3">{run.results.map((result) => <article key={result.id} className="rounded-md bg-slate-50 p-3"><div className="flex justify-between gap-3"><h5 className="font-semibold">{result.question}</h5><span className="text-xs">{Object.entries(result.typeStatuses).map(([type,status]) => `${type}:${status}`).join(' · ')}</span></div>{result.answer ? <p className="mt-2 text-sm">{result.answer}</p> : null}{result.errorMessage ? <p className="mt-2 text-sm text-[var(--color-danger)]">{result.errorMessage}</p> : null}<MetricBlock title="检索指标" value={result.retrievalMetrics} /><MetricBlock title="回答指标" value={result.answerMetrics} /><MetricBlock title="RAGAS" value={result.ragasMetrics} /></article>)}</div>
  </div>
}

function RetrievalDebugSection() {
  const mutation = useRetrievalDebugMutation()
  const knowledgeBases = useKnowledgeBasesQuery()
  const [query, setQuery] = useState('')
  const [kbId, setKbId] = useState('')
  return <div className="rounded-xl border border-[var(--color-border)] bg-white p-5 shadow-sm"><h3 className="flex items-center gap-2 text-lg font-semibold"><Bug className="size-5" />检索调试</h3><form onSubmit={(event) => { event.preventDefault(); mutation.mutate({ query: query.trim(), knowledgeBaseIds: [kbId], queryMode: 'hybrid' }) }} className="mt-4 flex flex-col gap-3 md:flex-row"><input aria-label="调试查询" value={query} onChange={(event) => setQuery(event.target.value)} className="min-h-11 flex-1 rounded-md border px-3" /><select aria-label="调试知识库" value={kbId} onChange={(event) => setKbId(event.target.value)} className="min-h-11 rounded-md border px-3"><option value="">请选择知识库</option>{knowledgeBases.data?.map((kb) => <option key={kb.id} value={kb.id}>{kb.name}</option>)}</select><Button type="submit" disabled={!query.trim() || !kbId || mutation.isPending}><Activity className="size-4" />运行调试</Button></form>
    {mutation.isError ? <Alert tone="danger" className="mt-3">{mutation.error.message}</Alert> : null}
    {mutation.data ? <div className="mt-4 overflow-x-auto"><table className="min-w-full text-left text-sm"><thead><tr>{['Rank','Retriever','Document / Chunk','Score','Rerank','Snippet'].map((x) => <th key={x} className="px-3 py-2">{x}</th>)}</tr></thead><tbody>{mutation.data.items.map((item, index) => <tr key={String(item.chunk_id ?? item.document_id ?? index)}><td className="px-3 py-3">{String(item.rank)}</td><td className="px-3 py-3">{String(item.retriever_type)}</td><td className="px-3 py-3">{String(item.document_title ?? item.document_id ?? '—')}<br/><span className="text-xs">{String(item.chunk_id ?? '')}</span></td><td className="px-3 py-3">{String(item.score ?? '—')}</td><td className="px-3 py-3">{String(item.rerank_score ?? '—')}</td><td className="max-w-md px-3 py-3">{String(item.snippet)}</td></tr>)}</tbody></table></div> : null}
  </div>
}

function Field({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) { return <label className="block text-sm font-semibold">{label}<input value={value} onChange={(event) => onChange(event.target.value)} className="mt-2 min-h-11 w-full rounded-md border border-[var(--color-border)] px-3 font-normal" /></label> }
function List({ title, items, loading }: { title: string; items: string[]; loading: boolean }) { return <div><h4 className="font-semibold">{title}</h4>{loading ? <div className="mt-2"><LoadingState message="正在加载…" minH="min-h-0" /></div> : <ul className="mt-2 space-y-2 text-sm">{items.map((item,index) => <li key={`${item}-${index}`} className="rounded-md bg-slate-50 p-2">{item}</li>)}</ul>}</div> }
function splitCsv(value: string) { return value.split(',').map((item) => item.trim()).filter(Boolean) }
function CheckGroup({ title, options, selected, setSelected }: { title: string; options: string[][]; selected: string[]; setSelected: (values: string[]) => void }) { return <fieldset><legend className="text-sm font-semibold">{title}</legend><div className="mt-2 max-h-32 space-y-1 overflow-auto">{options.map(([value,label]) => <label key={value} className="flex gap-2 text-xs"><input type="checkbox" checked={selected.includes(value)} onChange={(event) => setSelected(event.target.checked ? [...selected,value] : selected.filter((item) => item !== value))} />{label}</label>)}</div></fieldset> }
function Status({ value }: { value: string }) { return <span className="rounded-full bg-[var(--color-primary-50)] px-2 py-1 text-xs font-semibold text-[var(--color-primary-700)]">{value}</span> }
function MetricBlock({ title, value }: { title: string; value: Record<string, unknown> | null }) { if (!value) return null; const status = typeof value.status === 'string' ? value.status : null; return <div className="mt-2 text-xs"><span className="font-semibold">{title}：</span>{status === 'skipped' || status === 'disabled' ? <span>{status}（{String(value.reason ?? '无原因')}）</span> : <span>{Object.entries(value).map(([key,item]) => `${key}=${String(item)}`).join(' · ')}</span>}</div> }
