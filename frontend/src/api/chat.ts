import { apiClient } from './client'
import type { QueryMode } from './knowledge-bases'

export type Citation = {
  knowledgeBaseId: string
  knowledgeBaseName: string
  documentId: string | null
  documentTitle: string | null
  chunkId: string | null
  snippet: string
  score: number | null
}

export type RouterResult = {
  domain: string
  taskType: string
  riskLevel: string
}

export type ComplianceResult = {
  passed: boolean
  warnings: string[]
}

export type AssistantMetadata = {
  citations: Citation[]
  queryLogId: string | null
  confidenceScore: number | null
  queryMode: string | null
  routerResult: RouterResult | null
  suggestedSkills: Array<{ name: string; description: string }>
  compliance: ComplianceResult | null
}

export type ConversationMessage = {
  id: string
  role: string
  content: string
  metadata: AssistantMetadata
  createdAt: string
}

export type Conversation = {
  id: string
  title: string
  status: string
  summary: Record<string, unknown>
  messages: ConversationMessage[]
}

export type ChatResult = {
  conversationId: string
  messageId: string
  queryLogId: string
  answer: string
  citations: Citation[]
  confidenceScore: number
  queryMode: string
  routerResult: RouterResult
  suggestedSkills: Array<{ name: string; description: string }>
  compliance: ComplianceResult
}

export type SendChatInput = {
  conversationId?: string
  question: string
  knowledgeBaseIds: string[]
  queryMode: QueryMode
}

export type FeedbackRating =
  | 'useful'
  | 'not_useful'
  | 'wrong_citation'
  | 'incomplete'

export type QueryFeedback = {
  id: string
  queryLogId: string
  userId: string
  rating: FeedbackRating
  comment: string | null
  createdAt: string
  updatedAt: string
}

type CitationRaw = {
  knowledge_base_id: string
  knowledge_base_name: string
  document_id: string | null
  document_title: string | null
  chunk_id: string | null
  snippet: string
  score: number | null
}

type RouterResultRaw = {
  domain: string
  task_type: string
  risk_level: string
}

type ComplianceRaw = { passed: boolean; warnings: string[] }

type AssistantMetadataRaw = {
  citations: CitationRaw[]
  query_log_id: string | null
  confidence_score: number | null
  query_mode: string | null
  router_result: RouterResultRaw | null
  suggested_skills: Array<{ name: string; description: string }>
  compliance: ComplianceRaw | null
}

function toCitation(raw: CitationRaw): Citation {
  return {
    knowledgeBaseId: raw.knowledge_base_id,
    knowledgeBaseName: raw.knowledge_base_name,
    documentId: raw.document_id,
    documentTitle: raw.document_title,
    chunkId: raw.chunk_id,
    snippet: raw.snippet,
    score: raw.score,
  }
}

function toRouterResult(raw: RouterResultRaw): RouterResult {
  return {
    domain: raw.domain,
    taskType: raw.task_type,
    riskLevel: raw.risk_level,
  }
}

function toCompliance(raw: ComplianceRaw): ComplianceResult {
  return { passed: raw.passed, warnings: raw.warnings }
}

function toMetadata(raw: AssistantMetadataRaw): AssistantMetadata {
  return {
    citations: raw.citations.map(toCitation),
    queryLogId: raw.query_log_id,
    confidenceScore: raw.confidence_score,
    queryMode: raw.query_mode,
    routerResult: raw.router_result ? toRouterResult(raw.router_result) : null,
    suggestedSkills: raw.suggested_skills,
    compliance: raw.compliance ? toCompliance(raw.compliance) : null,
  }
}

export async function sendChat(input: SendChatInput): Promise<ChatResult> {
  const raw = await apiClient.request<{
    conversation_id: string
    message_id: string
    query_log_id: string
    answer: string
    citations: CitationRaw[]
    confidence_score: number
    query_mode: string
    router_result: RouterResultRaw
    suggested_skills: Array<{ name: string; description: string }>
    compliance: ComplianceRaw
  }>('/api/chat', {
    method: 'POST',
    body: JSON.stringify({
      conversation_id: input.conversationId ?? null,
      question: input.question,
      knowledge_base_ids: input.knowledgeBaseIds,
      enable_skill: true,
      retrieval_strategy: 'lightrag_only',
      query_mode: input.queryMode,
      top_k: 5,
    }),
  })
  return {
    conversationId: raw.conversation_id,
    messageId: raw.message_id,
    queryLogId: raw.query_log_id,
    answer: raw.answer,
    citations: raw.citations.map(toCitation),
    confidenceScore: raw.confidence_score,
    queryMode: raw.query_mode,
    routerResult: toRouterResult(raw.router_result),
    suggestedSkills: raw.suggested_skills,
    compliance: toCompliance(raw.compliance),
  }
}

export async function getConversation(
  conversationId: string,
  signal?: AbortSignal,
): Promise<Conversation> {
  const raw = await apiClient.request<{
    id: string
    title: string
    status: string
    summary: Record<string, unknown>
    messages: Array<{
      id: string
      role: string
      content: string
      meta_json: AssistantMetadataRaw
      created_at: string
    }>
  }>(`/api/conversations/${encodeURIComponent(conversationId)}`, { signal })
  return {
    id: raw.id,
    title: raw.title,
    status: raw.status,
    summary: raw.summary,
    messages: raw.messages.map((message) => ({
      id: message.id,
      role: message.role,
      content: message.content,
      metadata: toMetadata(message.meta_json),
      createdAt: message.created_at,
    })),
  }
}

export async function submitFeedback(
  queryLogId: string,
  rating: FeedbackRating,
  comment?: string,
): Promise<QueryFeedback> {
  const raw = await apiClient.request<{
    id: string
    query_log_id: string
    user_id: string
    rating: FeedbackRating
    comment: string | null
    created_at: string
    updated_at: string
  }>(`/api/query-logs/${encodeURIComponent(queryLogId)}/feedback`, {
    method: 'POST',
    body: JSON.stringify({ rating, comment: comment?.trim() || null }),
  })
  return {
    id: raw.id,
    queryLogId: raw.query_log_id,
    userId: raw.user_id,
    rating: raw.rating,
    comment: raw.comment,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
  }
}
