import { apiClient } from './client'
import type { QueryMode } from './knowledge-bases'

export type EvalCase = {
  id: string
  question: string
  category: string
  expectedAnswerKeywords: string[]
  expectedSourceDocuments: string[]
  expectedChunkIds: string[]
  shouldAnswer: boolean
  enabled: boolean
  createdAt: string
}

export type RetrievalItem = {
  id: string
  evalCaseId: string | null
  query: string
  knowledgeBaseIds: string[]
  relevantDocumentIds: string[]
  relevantChunkIds: string[]
  enabled: boolean
  createdAt: string
}

export type EvalResult = {
  id: string
  question: string
  answer: string | null
  retrievedSources: Array<Record<string, unknown>>
  retrievalMetrics: Record<string, unknown> | null
  answerMetrics: Record<string, unknown> | null
  ragasMetrics: Record<string, unknown> | null
  typeStatuses: Record<string, string>
  score: number
  passed: boolean
  errorMessage: string | null
  latencyMs: number
}

export type EvalRunScope = {
  knowledgeBases: Array<{ id: string; code: string; name: string }>
  taskTypes: string[]
  sources: string[]
  itemCount: number
  caseCount: number
  staleGoldCount: number
  label: string | null
}

export type EvalRun = {
  id: string
  name: string
  status: string
  totalCases: number
  metrics: Record<string, unknown>
  configSnapshot: Record<string, unknown>
  createdBy: string | null
  createdAt: string
  startedAt: string | null
  finishedAt: string | null
  errorSummary: string | null
  requestId: string | null
  scope: EvalRunScope | null
  results: EvalResult[]
}

export type EvalRunSummary = Omit<EvalRun, 'configSnapshot' | 'results'>
export type EvalRunList = {
  items: EvalRunSummary[]
  total: number
  page: number
  pageSize: number
}

type EvalCaseRaw = {
  id: string
  question: string
  category: string
  expected_answer_keywords: string[]
  expected_source_documents: string[]
  expected_chunk_ids: string[]
  should_answer: boolean
  enabled: boolean
  created_at: string
}

function toCase(raw: EvalCaseRaw): EvalCase {
  return {
    id: raw.id,
    question: raw.question,
    category: raw.category,
    expectedAnswerKeywords: raw.expected_answer_keywords,
    expectedSourceDocuments: raw.expected_source_documents,
    expectedChunkIds: raw.expected_chunk_ids,
    shouldAnswer: raw.should_answer,
    enabled: raw.enabled,
    createdAt: raw.created_at,
  }
}

type RetrievalRaw = {
  id: string
  eval_case_id: string | null
  query: string
  knowledge_base_ids: string[]
  relevant_document_ids: string[]
  relevant_chunk_ids: string[]
  enabled: boolean
  created_at: string
}

type EvalResultRaw = {
  id: string
  question: string
  answer: string | null
  retrieved_sources: Array<Record<string, unknown>>
  retrieval_metrics: Record<string, unknown> | null
  answer_metrics: Record<string, unknown> | null
  ragas_metrics: Record<string, unknown> | null
  type_statuses: Record<string, string>
  score: number
  passed: boolean
  error_message: string | null
  latency_ms: number
}

type EvalRunScopeRaw = {
  knowledge_bases?: Array<{ id: string; code: string; name: string }>
  task_types?: string[]
  sources?: string[]
  item_count?: number
  case_count?: number
  stale_gold_count?: number
  label?: string | null
}

type EvalRunRaw = {
  id: string
  name: string
  status: string
  total_cases: number
  metrics: Record<string, unknown>
  config_snapshot: Record<string, unknown>
  created_by: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  error_summary: string | null
  request_id: string | null
  scope?: EvalRunScopeRaw | null
  results: EvalResultRaw[]
}

type EvalRunSummaryRaw = Omit<EvalRunRaw, 'config_snapshot' | 'results'>

function toRetrieval(raw: RetrievalRaw): RetrievalItem {
  return {
    id: raw.id,
    evalCaseId: raw.eval_case_id,
    query: raw.query,
    knowledgeBaseIds: raw.knowledge_base_ids,
    relevantDocumentIds: raw.relevant_document_ids,
    relevantChunkIds: raw.relevant_chunk_ids,
    enabled: raw.enabled,
    createdAt: raw.created_at,
  }
}

function toResult(raw: EvalResultRaw): EvalResult {
  return {
    id: raw.id,
    question: raw.question,
    answer: raw.answer,
    retrievedSources: raw.retrieved_sources,
    retrievalMetrics: raw.retrieval_metrics,
    answerMetrics: raw.answer_metrics,
    ragasMetrics: raw.ragas_metrics,
    typeStatuses: raw.type_statuses,
    score: raw.score,
    passed: raw.passed,
    errorMessage: raw.error_message,
    latencyMs: raw.latency_ms,
  }
}

function toScope(raw: EvalRunScopeRaw | null | undefined): EvalRunScope | null {
  if (!raw) return null
  return {
    knowledgeBases: (raw.knowledge_bases ?? []).map((item) => ({
      id: item.id,
      code: item.code,
      name: item.name,
    })),
    taskTypes: raw.task_types ?? [],
    sources: raw.sources ?? [],
    itemCount: raw.item_count ?? 0,
    caseCount: raw.case_count ?? 0,
    staleGoldCount: raw.stale_gold_count ?? 0,
    label: raw.label ?? null,
  }
}

function toRunSummary(raw: EvalRunSummaryRaw): EvalRunSummary {
  return {
    id: raw.id,
    name: raw.name,
    status: raw.status,
    totalCases: raw.total_cases,
    metrics: raw.metrics,
    createdBy: raw.created_by,
    createdAt: raw.created_at,
    startedAt: raw.started_at,
    finishedAt: raw.finished_at,
    errorSummary: raw.error_summary,
    requestId: raw.request_id,
    scope: toScope(raw.scope),
  }
}

function toRun(raw: EvalRunRaw): EvalRun {
  return {
    ...toRunSummary(raw),
    configSnapshot: raw.config_snapshot,
    results: (raw.results ?? []).map(toResult),
  }
}

export const listEvalCases = async (signal?: AbortSignal) =>
  (await apiClient.request<EvalCaseRaw[]>('/api/eval/cases', { signal })).map(toCase)

export async function createEvalCase(input: {
  question: string
  category: string
  expectedAnswerKeywords: string[]
  expectedSourceDocuments: string[]
  shouldAnswer: boolean
}): Promise<EvalCase> {
  return toCase(await apiClient.request<EvalCaseRaw>('/api/eval/cases', {
    method: 'POST',
    body: JSON.stringify({
      question: input.question,
      category: input.category,
      expected_answer_keywords: input.expectedAnswerKeywords,
      expected_source_documents: input.expectedSourceDocuments,
      expected_chunk_ids: [],
      should_answer: input.shouldAnswer,
    }),
  }))
}

export const listRetrievalItems = async (signal?: AbortSignal) =>
  (await apiClient.request<RetrievalRaw[]>('/api/eval/retrieval-items', { signal }))
    .map(toRetrieval)

export async function createRetrievalItem(input: {
  evalCaseId?: string
  query: string
  knowledgeBaseIds: string[]
  relevantDocumentIds: string[]
}): Promise<RetrievalItem> {
  return toRetrieval(await apiClient.request<RetrievalRaw>('/api/eval/retrieval-items', {
    method: 'POST',
    body: JSON.stringify({
      eval_case_id: input.evalCaseId ?? null,
      query: input.query,
      knowledge_base_ids: input.knowledgeBaseIds,
      relevant_document_ids: input.relevantDocumentIds,
      relevant_chunk_ids: [],
      relevance_judgement: null,
    }),
  }))
}

export async function listEvalRuns(
  page: number,
  pageSize: number,
  status?: string,
  signal?: AbortSignal,
): Promise<EvalRunList> {
  const search = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  if (status) search.set('status', status)
  const raw = await apiClient.request<{
    items: EvalRunSummaryRaw[]
    total: number
    page: number
    page_size: number
  }>(
    `/api/eval/runs?${search.toString()}`,
    { signal },
  )
  return {
    items: raw.items.map(toRunSummary),
    total: raw.total,
    page: raw.page,
    pageSize: raw.page_size,
  }
}

export const getEvalRun = async (id: string, signal?: AbortSignal) =>
  toRun(await apiClient.request<EvalRunRaw>(
    `/api/eval/runs/${encodeURIComponent(id)}`,
    { signal },
  ))

export async function deleteEvalRun(id: string): Promise<void> {
  await apiClient.request(`/api/eval/runs/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}

export async function exportEvalRun(
  id: string,
  format: 'json' | 'csv' = 'json',
): Promise<Blob> {
  const path = `/api/eval/runs/${encodeURIComponent(id)}/export?format=${format}`
  const headers = new Headers()
  const token = apiClient.getAccessToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(apiClient.resolveRequestUrl(path), { headers })
  if (!response.ok) {
    const text = await response.text()
    throw new Error(text || `Export failed (${response.status})`)
  }
  return response.blob()
}

export async function createEvalRun(input: {
  name: string
  caseIds: string[]
  retrievalItemIds: string[]
  evalTypes: Array<'retrieval' | 'rag_answer' | 'ragas'>
  queryMode: QueryMode
  strategy?: string
  compareStrategies?: string[]
  ragasEnabled?: boolean
  rerankEnabled?: boolean
}): Promise<EvalRun> {
  const ragasEnabled =
    input.ragasEnabled ?? input.evalTypes.includes('ragas')
  return toRun(await apiClient.request<EvalRunRaw>('/api/eval/runs', {
    method: 'POST',
    body: JSON.stringify({
      name: input.name,
      case_ids: input.caseIds,
      retrieval_item_ids: input.retrievalItemIds,
      eval_types: input.evalTypes,
      retrieval_config: {
        strategy: input.strategy ?? 'hybrid_lightrag_bm25',
        top_k_values: [1, 3, 5, 10],
        rerank_enabled: Boolean(input.rerankEnabled),
        query_mode: input.queryMode,
      },
      compare_strategies: input.compareStrategies ?? [],
      ragas_config: {
        enabled: ragasEnabled,
        metrics: ['faithfulness', 'answer_relevancy', 'context_precision'],
      },
    }),
  }))
}

export type CrudImportResult = {
  knowledgeBaseId: string
  taskType: string
  documentsCreated: number
  documentsReused: number
  distractorDocumentsCreated: number
  retrievalItemsCreated: number
  evalCasesCreated: number
  indexed: number
  indexFailed: number
  indexQueued: number
  sampleSize: number
  corpusDocumentCount: number
  sourcePath: string
  warning: string | null
}

export async function importCrudDataset(input: {
  knowledgeBaseId?: string
  sourcePath?: string
  taskType?: 'questanswer_1doc' | 'questanswer_2docs' | 'questanswer_3docs'
  sampleSize?: number
  distractorCount?: number
  createEvalCases?: boolean
  indexDocuments?: boolean
  useEvalTestKb?: boolean
}): Promise<CrudImportResult> {
  // Import used to block on LightRAG indexing and exceed the default 60s timeout,
  // leaving the button spinning. Backend now queues indexing; keep a generous
  // timeout for large JSON parse + DB writes only.
  const raw = await apiClient.request<{
    knowledge_base_id: string
    task_type: string
    documents_created: number
    documents_reused: number
    distractor_documents_created?: number
    retrieval_items_created: number
    eval_cases_created: number
    indexed: number
    index_failed: number
    index_queued?: number
    sample_size: number
    corpus_document_count?: number
    source_path: string
    warning?: string | null
  }>('/api/eval/datasets/crud-import', {
    method: 'POST',
    timeoutMs: 180_000,
    body: JSON.stringify({
      knowledge_base_id: input.knowledgeBaseId || null,
      source_path: input.sourcePath || null,
      task_type: input.taskType ?? 'questanswer_1doc',
      sample_size: input.sampleSize ?? 50,
      distractor_count: input.distractorCount ?? 200,
      create_eval_cases: input.createEvalCases ?? true,
      index_documents: input.indexDocuments ?? true,
      use_eval_test_kb: input.useEvalTestKb ?? true,
      offset: 0,
    }),
  })
  return {
    knowledgeBaseId: raw.knowledge_base_id,
    taskType: raw.task_type,
    documentsCreated: raw.documents_created,
    documentsReused: raw.documents_reused,
    distractorDocumentsCreated: raw.distractor_documents_created ?? 0,
    retrievalItemsCreated: raw.retrieval_items_created,
    evalCasesCreated: raw.eval_cases_created,
    indexed: raw.indexed,
    indexFailed: raw.index_failed,
    indexQueued: raw.index_queued ?? 0,
    sampleSize: raw.sample_size,
    corpusDocumentCount: raw.corpus_document_count ?? 0,
    sourcePath: raw.source_path,
    warning: raw.warning ?? null,
  }
}

export async function retrievalDebug(input: {
  query: string
  knowledgeBaseIds: string[]
  queryMode: QueryMode
}): Promise<{
  query: string
  items: Array<Record<string, unknown>>
  warnings: string[]
}> {
  return apiClient.request<{
    query: string
    items: Array<Record<string, unknown>>
    warnings: string[]
  }>('/api/eval/retrieval-debug', {
    method: 'POST',
    body: JSON.stringify({
      query: input.query,
      knowledge_base_ids: input.knowledgeBaseIds,
      strategy: 'lightrag_only',
      top_k: 10,
      rerank_enabled: false,
      query_mode: input.queryMode,
    }),
  })
}
