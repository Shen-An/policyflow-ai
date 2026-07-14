import { apiClient } from './client'
import { AppError } from './errors'
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
  needSkill?: boolean
  toolHints?: string[]
  rewriteQuery?: string | null
}

export type ComplianceResult = {
  passed: boolean
  warnings: string[]
}

export type UsedMemoryItem = {
  id: string | null
  memoryType: string
  content: string
  sourceSlot: 'fixed' | 'recalled' | 'rolling_summary' | 'history' | string
  confidence: number | null
}

export type ToolCallTrace = {
  toolName: string
  status: string
  agentName: string | null
  inputSummary: Record<string, unknown>
  outputSummary: Record<string, unknown>
  errorMessage: string | null
  latencyMs: number
}

export type CommandTrace = {
  name: string
  status: string
  summary: string
  output: Record<string, unknown>
}

export type TurnDiagnostics = {
  memories: UsedMemoryItem[]
  tools: ToolCallTrace[]
  commands: CommandTrace[]
}

export type AssistantMetadata = {
  citations: Citation[]
  queryLogId: string | null
  confidenceScore: number | null
  queryMode: string | null
  routerResult: RouterResult | null
  suggestedSkills: Array<{ name: string; description: string }>
  compliance: ComplianceResult | null
  diagnostics: TurnDiagnostics
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
  createdAt?: string
  updatedAt?: string
}

export type ConversationSummary = {
  id: string
  title: string
  status: string
  messageCount: number
  lastMessagePreview: string | null
  lastMessageRole: string | null
  createdAt: string
  updatedAt: string
}

export type ConversationListResult = {
  items: ConversationSummary[]
  total: number
  page: number
  pageSize: number
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
  diagnostics: TurnDiagnostics
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
  need_skill?: boolean
  tool_hints?: string[]
  rewrite_query?: string | null
}

type ComplianceRaw = { passed: boolean; warnings: string[] }

type UsedMemoryItemRaw = {
  id: string | null
  memory_type: string
  content: string
  source_slot: string
  confidence: number | null
}

type ToolCallTraceRaw = {
  tool_name: string
  status: string
  agent_name: string | null
  input_summary: Record<string, unknown>
  output_summary: Record<string, unknown>
  error_message: string | null
  latency_ms: number
}

type CommandTraceRaw = {
  name: string
  status: string
  summary: string
  output: Record<string, unknown>
}

type TurnDiagnosticsRaw = {
  memories?: UsedMemoryItemRaw[]
  tools?: ToolCallTraceRaw[]
  commands?: CommandTraceRaw[]
}

type AssistantMetadataRaw = {
  citations: CitationRaw[]
  query_log_id: string | null
  confidence_score: number | null
  query_mode: string | null
  router_result: RouterResultRaw | null
  suggested_skills: Array<{ name: string; description: string }>
  compliance: ComplianceRaw | null
  diagnostics?: TurnDiagnosticsRaw | null
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
    needSkill: raw.need_skill,
    toolHints: raw.tool_hints,
    rewriteQuery: raw.rewrite_query,
  }
}

function toCompliance(raw: ComplianceRaw): ComplianceResult {
  return { passed: raw.passed, warnings: raw.warnings }
}

function toDiagnostics(raw?: TurnDiagnosticsRaw | null): TurnDiagnostics {
  return {
    memories: (raw?.memories ?? []).map((item) => ({
      id: item.id,
      memoryType: item.memory_type,
      content: item.content,
      sourceSlot: item.source_slot,
      confidence: item.confidence,
    })),
    tools: (raw?.tools ?? []).map((item) => ({
      toolName: item.tool_name,
      status: item.status,
      agentName: item.agent_name,
      inputSummary: item.input_summary ?? {},
      outputSummary: item.output_summary ?? {},
      errorMessage: item.error_message,
      latencyMs: item.latency_ms ?? 0,
    })),
    commands: (raw?.commands ?? []).map((item) => ({
      name: item.name,
      status: item.status,
      summary: item.summary ?? '',
      output: item.output ?? {},
    })),
  }
}

function toMetadata(raw: AssistantMetadataRaw): AssistantMetadata {
  return {
    citations: (raw.citations ?? []).map(toCitation),
    queryLogId: raw.query_log_id,
    confidenceScore: raw.confidence_score,
    queryMode: raw.query_mode,
    routerResult: raw.router_result ? toRouterResult(raw.router_result) : null,
    suggestedSkills: raw.suggested_skills ?? [],
    compliance: raw.compliance ? toCompliance(raw.compliance) : null,
    diagnostics: toDiagnostics(raw.diagnostics),
  }
}

/** Chat may span multi-KB retrieval + LLM generation; allow up to 3 minutes. */
const CHAT_REQUEST_TIMEOUT_MS = 180_000

function mapChatResult(raw: {
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
  diagnostics?: TurnDiagnosticsRaw | null
}): ChatResult {
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
    diagnostics: toDiagnostics(raw.diagnostics),
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
    diagnostics?: TurnDiagnosticsRaw | null
  }>('/api/chat', {
    method: 'POST',
    timeoutMs: CHAT_REQUEST_TIMEOUT_MS,
    body: JSON.stringify({
      conversation_id: input.conversationId ?? null,
      question: input.question,
      knowledge_base_ids: input.knowledgeBaseIds,
      enable_skill: true,
      retrieval_strategy: 'hybrid_lightrag_bm25',
      query_mode: input.queryMode,
      top_k: 5,
    }),
  })
  return mapChatResult(raw)
}

export type ChatStageEvent = {
  stage: string
  status: string
  message: string
}

export type ChatStreamHandlers = {
  onStage?: (event: ChatStageEvent) => void
  onDiagnosticsPartial?: (partial: Partial<TurnDiagnostics>) => void
  onDiagnostics?: (diagnostics: TurnDiagnostics) => void
  signal?: AbortSignal
}

function parseSseChunk(chunk: string): Array<{ event: string; data: string }> {
  const events: Array<{ event: string; data: string }> = []
  const blocks = chunk.split(/\n\n/)
  for (const block of blocks) {
    const trimmed = block.trim()
    if (!trimmed) continue
    let event = 'message'
    const dataLines: string[] = []
    for (const line of trimmed.split(/\n/)) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
    }
    if (dataLines.length) events.push({ event, data: dataLines.join('\n') })
  }
  return events
}

function mergeDiagnosticsPartial(
  current: TurnDiagnostics,
  partial: TurnDiagnosticsRaw | Partial<TurnDiagnosticsRaw> | null | undefined,
): TurnDiagnostics {
  if (!partial) return current
  const next: TurnDiagnostics = {
    memories: current.memories,
    tools: current.tools,
    commands: [...current.commands],
  }
  if (partial.memories) {
    next.memories = toDiagnostics({ memories: partial.memories }).memories
  }
  if (partial.tools) {
    const mapped = toDiagnostics({ tools: partial.tools }).tools
    // Replace by toolName if present, else append.
    const byName = new Map(next.tools.map((item) => [item.toolName, item]))
    for (const item of mapped) byName.set(item.toolName, item)
    next.tools = Array.from(byName.values())
  }
  if (partial.commands) {
    const mapped = toDiagnostics({ commands: partial.commands }).commands
    const byName = new Map(next.commands.map((item) => [item.name, item]))
    for (const item of mapped) byName.set(item.name, item)
    // Keep a stable-ish pipeline order.
    const order = [
      'MemoryLoad',
      'RouterAgent',
      'RetrievalAgent',
      'AnswerAgent',
      'SkillAgent',
      'ComplianceAgent',
      'MemoryWriteback',
    ]
    const rest = Array.from(byName.keys()).filter((name) => !order.includes(name))
    next.commands = [...order, ...rest]
      .map((name) => byName.get(name))
      .filter((item): item is CommandTrace => Boolean(item))
  }
  return next
}

export async function sendChatStream(
  input: SendChatInput,
  handlers: ChatStreamHandlers = {},
): Promise<ChatResult> {
  const controller = new AbortController()
  const timeout = globalThis.setTimeout(() => controller.abort('timeout'), CHAT_REQUEST_TIMEOUT_MS)
  const externalSignal = handlers.signal
  const abortFromExternal = () => controller.abort(externalSignal?.reason)
  externalSignal?.addEventListener('abort', abortFromExternal, { once: true })

  const headers = new Headers({
    Accept: 'text/event-stream',
    'Content-Type': 'application/json',
  })
  const accessToken = apiClient.getAccessToken()
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)

  const url = apiClient.resolveRequestUrl('/api/chat/stream')

  try {
    const response = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        conversation_id: input.conversationId ?? null,
        question: input.question,
        knowledge_base_ids: input.knowledgeBaseIds,
        enable_skill: true,
        retrieval_strategy: 'hybrid_lightrag_bm25',
        query_mode: input.queryMode,
        top_k: 5,
      }),
      signal: controller.signal,
    })

    if (!response.ok) {
      const text = await response.text()
      let message = `请求失败（${response.status}）`
      try {
        const parsed = JSON.parse(text) as { error?: { message?: string }; message?: string }
        message = parsed.error?.message || parsed.message || message
      } catch {
        if (text) message = text.slice(0, 200)
      }
      throw new AppError({
        kind: response.status === 401 ? 'auth' : 'server',
        code: 'CHAT_STREAM_HTTP_ERROR',
        message,
        status: response.status,
        retryable: response.status >= 500,
      })
    }
    if (!response.body) {
      throw new AppError({
        kind: 'network',
        code: 'CHAT_STREAM_EMPTY',
        message: '流式响应为空。',
        retryable: true,
      })
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    let finalResult: ChatResult | null = null
    let liveDiagnostics: TurnDiagnostics = { memories: [], tools: [], commands: [] }

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() ?? ''
      for (const part of parts) {
        for (const evt of parseSseChunk(part)) {
          let payload: Record<string, unknown> = {}
          try {
            payload = JSON.parse(evt.data) as Record<string, unknown>
          } catch {
            continue
          }
          if (evt.event === 'stage') {
            handlers.onStage?.({
              stage: String(payload.stage ?? ''),
              status: String(payload.status ?? ''),
              message: String(payload.message ?? ''),
            })
          } else if (evt.event === 'diagnostics_partial') {
            liveDiagnostics = mergeDiagnosticsPartial(
              liveDiagnostics,
              payload as TurnDiagnosticsRaw,
            )
            handlers.onDiagnosticsPartial?.(liveDiagnostics)
          } else if (evt.event === 'diagnostics') {
            liveDiagnostics = toDiagnostics(payload as TurnDiagnosticsRaw)
            handlers.onDiagnostics?.(liveDiagnostics)
          } else if (evt.event === 'final') {
            finalResult = mapChatResult(
              payload as {
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
                diagnostics?: TurnDiagnosticsRaw | null
              },
            )
          } else if (evt.event === 'error') {
            throw new AppError({
              kind: 'server',
              code: String(payload.code ?? 'CHAT_STREAM_ERROR'),
              message: String(payload.message ?? '流式问答失败'),
              status: Number(payload.status_code ?? 500),
              retryable: true,
            })
          }
        }
      }
    }

    if (!finalResult) {
      throw new AppError({
        kind: 'server',
        code: 'CHAT_STREAM_INCOMPLETE',
        message: '流式问答未返回最终结果。',
        retryable: true,
      })
    }
    return finalResult
  } catch (error) {
    if (error instanceof AppError) throw error
    if (controller.signal.aborted && controller.signal.reason === 'timeout') {
      throw new AppError({
        kind: 'timeout',
        code: 'REQUEST_TIMEOUT',
        message: `请求超时（${CHAT_REQUEST_TIMEOUT_MS / 1000} 秒内未完成）。制度问答可能因检索/模型生成较慢，请稍后重试。`,
        retryable: true,
      })
    }
    if (controller.signal.aborted) {
      throw new AppError({
        kind: 'network',
        code: 'REQUEST_ABORTED',
        message: '请求已取消。',
        retryable: false,
      })
    }
    throw new AppError({
      kind: 'network',
      code: 'NETWORK_ERROR',
      message: '网络连接失败，请检查网络后重试。',
      details: error,
      retryable: true,
    })
  } finally {
    globalThis.clearTimeout(timeout)
    externalSignal?.removeEventListener('abort', abortFromExternal)
  }
}

type ConversationSummaryRaw = {
  id: string
  title: string
  status: string
  message_count: number
  last_message_preview: string | null
  last_message_role: string | null
  created_at: string
  updated_at: string
}

function toConversationSummary(item: ConversationSummaryRaw): ConversationSummary {
  return {
    id: item.id,
    title: item.title,
    status: item.status,
    messageCount: item.message_count,
    lastMessagePreview: item.last_message_preview,
    lastMessageRole: item.last_message_role,
    createdAt: item.created_at,
    updatedAt: item.updated_at,
  }
}

export async function listConversations(
  page = 1,
  pageSize = 20,
  keyword = '',
  signal?: AbortSignal,
): Promise<ConversationListResult> {
  const search = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  })
  const normalizedKeyword = keyword.trim()
  if (normalizedKeyword) search.set('keyword', normalizedKeyword)
  const raw = await apiClient.request<{
    items: ConversationSummaryRaw[]
    total: number
    page: number
    page_size: number
  }>(`/api/conversations?${search.toString()}`, { signal })
  return {
    items: raw.items.map(toConversationSummary),
    total: raw.total,
    page: raw.page,
    pageSize: raw.page_size,
  }
}

export async function renameConversation(
  conversationId: string,
  title: string,
): Promise<ConversationSummary> {
  const raw = await apiClient.request<ConversationSummaryRaw>(
    `/api/conversations/${encodeURIComponent(conversationId)}`,
    {
      method: 'PATCH',
      body: JSON.stringify({ title: title.trim() }),
    },
  )
  return toConversationSummary(raw)
}

export async function deleteConversation(conversationId: string): Promise<void> {
  await apiClient.request<void>(
    `/api/conversations/${encodeURIComponent(conversationId)}`,
    { method: 'DELETE' },
  )
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
    created_at?: string
    updated_at?: string
  }>(`/api/conversations/${encodeURIComponent(conversationId)}`, { signal })
  return {
    id: raw.id,
    title: raw.title,
    status: raw.status,
    summary: raw.summary,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
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
