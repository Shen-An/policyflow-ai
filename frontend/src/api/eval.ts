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

function toRun(raw: EvalRunRaw): EvalRun {
  return {
    id: raw.id,
    name: raw.name,
    status: raw.status,
    totalCases: raw.total_cases,
    metrics: raw.metrics,
    configSnapshot: raw.config_snapshot,
    createdBy: raw.created_by,
    createdAt: raw.created_at,
    startedAt: raw.started_at,
    finishedAt: raw.finished_at,
    errorSummary: raw.error_summary,
    requestId: raw.request_id,
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
    items: raw.items.map((item) => ({
      id: item.id,
      name: item.name,
      status: item.status,
      totalCases: item.total_cases,
      metrics: item.metrics,
      createdBy: item.created_by,
      createdAt: item.created_at,
      startedAt: item.started_at,
      finishedAt: item.finished_at,
      errorSummary: item.error_summary,
      requestId: item.request_id,
    })),
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

export async function createEvalRun(input: {
  name: string
  caseIds: string[]
  retrievalItemIds: string[]
  evalTypes: Array<'retrieval' | 'rag_answer' | 'ragas'>
  queryMode: QueryMode
}): Promise<EvalRun> {
  return toRun(await apiClient.request<EvalRunRaw>('/api/eval/runs', {
    method: 'POST',
    body: JSON.stringify({
      name: input.name,
      case_ids: input.caseIds,
      retrieval_item_ids: input.retrievalItemIds,
      eval_types: input.evalTypes,
      retrieval_config: {
        strategy: 'lightrag_only',
        top_k_values: [1, 3, 5],
        rerank_enabled: false,
        query_mode: input.queryMode,
      },
      ragas_config: { enabled: false, metrics: [] },
    }),
  }))
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
