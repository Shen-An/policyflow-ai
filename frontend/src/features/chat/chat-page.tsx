import { BookOpen, ChatCircle, CheckCircle, ClockCounterClockwise, Copy, Lightning, MagnifyingGlass, PaperPlaneTilt, PencilSimple, Plus, Trash, Warning } from '@phosphor-icons/react'
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Empty,
  Input,
  Modal,
  Select,
  Space,
  Typography,
} from 'antd'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import type {
  AssistantMetadata,
  ChatPlanEvent,
  ChatPlanOptionsEvent,
  ChatResult,
  ChatStageEvent,
  CommandTrace,
  ConversationMessage,
  ConversationSummary,
  FeedbackRating,
  PlanOption,
  PlanStep,
  ReasoningMode,
  ToolCallTrace,
  TurnDiagnostics,
  UsedMemoryItem,
} from '../../api/chat'
import type { QueryMode } from '../../api/knowledge-bases'
import { LoadingState } from '../../components/feedback/state-views'
import { MarkdownContent } from '../../components/markdown/markdown-content'
import { formatHistoryTime } from '../../lib/datetime'
import { palette } from '../../styles/palette'
import { useKnowledgeBasesQuery } from '../knowledge-bases/queries'
import {
  useConversationQuery,
  useConversationsQuery,
  useDeleteConversationMutation,
  useFeedbackMutation,
  useRenameConversationMutation,
  useSendChatMutation,
} from './queries'

const emptyMetadata: AssistantMetadata = {
  citations: [],
  queryLogId: null,
  confidenceScore: null,
  queryMode: null,
  routerResult: null,
  suggestedSkills: [],
  compliance: null,
  diagnostics: { memories: [], tools: [], commands: [] },
  turnStatus: null,
  reasoningMode: null,
  planOptions: [],
  pendingPlan: {},
  selectedOptionId: null,
}

function resultMessage(result: ChatResult): ConversationMessage {
  return {
    id: result.messageId,
    role: 'assistant',
    content: result.answer,
    createdAt: new Date().toISOString(),
    metadata: {
      citations: result.citations,
      queryLogId: result.queryLogId || null,
      confidenceScore: result.confidenceScore,
      queryMode: result.queryMode,
      routerResult: result.routerResult,
      suggestedSkills: result.suggestedSkills,
      compliance: result.compliance,
      diagnostics: result.diagnostics,
      turnStatus: result.status,
      reasoningMode: result.reasoningMode,
      planOptions: result.planOptions,
      pendingPlan: {},
      selectedOptionId: null,
    },
  }
}

const queryModeOptions = [
  { value: 'hybrid', label: '混合模式 — 根据问题智能选择' },
  { value: 'mix', label: '全局+局部混合 — 同时搜索索引与全文' },
  { value: 'local', label: '局部搜索 — 在相关索引片段中搜索' },
  { value: 'global', label: '全局搜索 — 在所有文档中全文搜索' },
  { value: 'naive', label: '朴素搜索 — 直接检索，不做语义优化' },
]

const queryModeLabels: Record<string, string> = {
  hybrid: '混合模式',
  mix: '全局+局部混合',
  local: '局部搜索',
  global: '全局搜索',
  naive: '朴素搜索',
}

async function copyTextToClipboard(text: string): Promise<void> {
  const value = text ?? ''
  if (!value) throw new Error('没有可复制的内容')
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value)
    return
  }
  const textarea = document.createElement('textarea')
  textarea.value = value
  textarea.setAttribute('readonly', 'true')
  textarea.style.position = 'fixed'
  textarea.style.left = '-9999px'
  document.body.appendChild(textarea)
  textarea.select()
  const ok = document.execCommand('copy')
  document.body.removeChild(textarea)
  if (!ok) throw new Error('复制失败')
}

export function ChatPage() {
  const { conversationId = '' } = useParams()
  const navigate = useNavigate()
  const { message, modal } = App.useApp()
  const [historyKeyword, setHistoryKeyword] = useState('')
  const [debouncedHistoryKeyword, setDebouncedHistoryKeyword] = useState('')
  const conversation = useConversationQuery(conversationId)
  const history = useConversationsQuery(1, 50, debouncedHistoryKeyword)
  const knowledgeBases = useKnowledgeBasesQuery()
  const sendMutation = useSendChatMutation()
  const renameMutation = useRenameConversationMutation()
  const deleteMutation = useDeleteConversationMutation()
  const [messages, setMessages] = useState<ConversationMessage[]>([])
  const [question, setQuestion] = useState('')
  const [selectedKnowledgeBases, setSelectedKnowledgeBases] = useState<string[]>([])
  const [queryMode, setQueryMode] = useState<QueryMode>('hybrid')
  const [failedQuestion, setFailedQuestion] = useState<string | null>(null)
  const [renaming, setRenaming] = useState<ConversationSummary | null>(null)
  const [renameTitle, setRenameTitle] = useState('')
  const [thinkingStages, setThinkingStages] = useState<ChatStageEvent[]>([])
  const [thinkingPlan, setThinkingPlan] = useState<ChatPlanEvent | null>(null)
  const [thinkingDiagnostics, setThinkingDiagnostics] = useState<TurnDiagnostics>({
    memories: [],
    tools: [],
    commands: [],
  })
  const [pendingPlanChoice, setPendingPlanChoice] = useState<ChatPlanOptionsEvent | null>(null)
  const [selectingOptionId, setSelectingOptionId] = useState<string | null>(null)
  const messagesContainerRef = useRef<HTMLDivElement | null>(null)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    setMessages([])
    setQuestion('')
    setFailedQuestion(null)
    setThinkingStages([])
    setThinkingPlan(null)
    setThinkingDiagnostics({ memories: [], tools: [], commands: [] })
    setPendingPlanChoice(null)
    setSelectingOptionId(null)
  }, [conversationId])

  // Rehydrate ToT picker from durable assistant stub metadata when reopening a conversation.
  useEffect(() => {
    const historyMessages = conversation.data?.messages ?? []
    if (!historyMessages.length) return
    const awaiting = [...historyMessages]
      .reverse()
      .find(
        (item) =>
          item.role === 'assistant' &&
          item.metadata.turnStatus === 'awaiting_plan_selection' &&
          (item.metadata.planOptions?.length ?? 0) > 0,
      )
    if (!awaiting) {
      setPendingPlanChoice(null)
      return
    }
    const options = awaiting.metadata.planOptions ?? []
    setPendingPlanChoice({
      difficulty: awaiting.metadata.routerResult?.difficulty ?? 'branched',
      reasoningMode: awaiting.metadata.reasoningMode ?? 'tot_select',
      options,
      recommendedOptionId: options.find((item) => item.recommended)?.id ?? null,
    })
  }, [conversation.data?.id, conversation.data?.messages])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedHistoryKeyword(historyKeyword.trim())
    }, 250)
    return () => window.clearTimeout(timer)
  }, [historyKeyword])

  const visibleMessages =
    messages.length > 0 ? messages : conversation.data?.messages ?? []

  useEffect(() => {
    // Open/refresh conversation and new messages should land at the latest turn.
    const frame = window.requestAnimationFrame(() => {
      const end = messagesEndRef.current
      if (end && typeof end.scrollIntoView === 'function') {
        end.scrollIntoView({ block: 'end', behavior: 'auto' })
      }
      const container = messagesContainerRef.current
      if (container) container.scrollTop = container.scrollHeight
    })
    return () => window.cancelAnimationFrame(frame)
  }, [
    conversationId,
    conversation.isPending,
    conversation.data?.id,
    visibleMessages.length,
    sendMutation.isPending,
    thinkingStages.length,
    thinkingPlan?.steps.length,
    pendingPlanChoice?.options.length,
  ])

  function clearThinkingState() {
    setThinkingStages([])
    setThinkingPlan(null)
    setThinkingDiagnostics({ memories: [], tools: [], commands: [] })
  }

  function applyStreamHandlers() {
    return {
      onStage: (event: ChatStageEvent) => {
        setThinkingStages((current) => {
          const next = [...current]
          const index = next.findIndex((item) => item.stage === event.stage)
          if (index >= 0) next[index] = event
          else next.push(event)
          return next
        })
      },
      onPlan: (plan: ChatPlanEvent) => {
        setThinkingPlan(plan)
      },
      onPlanStep: (step: { id: string; status: string; message?: string }) => {
        setThinkingPlan((current) => {
          if (!current) return current
          return {
            ...current,
            steps: current.steps.map((item) =>
              item.id === step.id
                ? {
                    ...item,
                    status: step.status,
                    message: step.message ?? item.message,
                  }
                : item,
            ),
          }
        })
      },
      onPlanOptions: (event: ChatPlanOptionsEvent) => {
        setPendingPlanChoice(event)
      },
      onDiagnosticsPartial: (partial: Partial<TurnDiagnostics>) => {
        setThinkingDiagnostics(partial as TurnDiagnostics)
      },
      onDiagnostics: (diagnostics: TurnDiagnostics) => {
        setThinkingDiagnostics(diagnostics)
      },
    }
  }

  async function handleCopy(content: string) {
    try {
      await copyTextToClipboard(content)
      message.success('已复制到剪贴板')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '复制失败')
    }
  }

  function handleEditQuestion(content: string) {
    setQuestion(content)
    window.requestAnimationFrame(() => {
      const el = document.getElementById('chat-question') as HTMLTextAreaElement | null
      if (!el) return
      el.focus()
      const cursor = content.length
      if (typeof el.setSelectionRange === 'function') {
        el.setSelectionRange(cursor, cursor)
      }
      if (typeof el.scrollIntoView === 'function') {
        el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
      }
    })
    message.info('问题已填入输入框，可修改后重新发送')
  }

  async function submit(value: string, appendUser = true) {
    const normalized = value.trim()
    if (!normalized || sendMutation.isPending) return
    if (appendUser) {
      setMessages((current) => [
        ...(current.length > 0 ? current : conversation.data?.messages ?? []),
        {
          id: `local-${Date.now()}`,
          role: 'user',
          content: normalized,
          createdAt: new Date().toISOString(),
          metadata: emptyMetadata,
        },
      ])
    }
    setQuestion('')
    setFailedQuestion(null)
    setPendingPlanChoice(null)
    clearThinkingState()
    try {
      const result = await sendMutation.mutateAsync({
        conversationId: conversationId || undefined,
        question: normalized,
        knowledgeBaseIds: selectedKnowledgeBases,
        queryMode,
        streamHandlers: applyStreamHandlers(),
      })
      clearThinkingState()
      if (result.status === 'awaiting_plan_selection') {
        setPendingPlanChoice({
          difficulty: result.routerResult.difficulty ?? 'branched',
          reasoningMode: result.reasoningMode || 'tot_select',
          options: result.planOptions,
          recommendedOptionId:
            result.planOptions.find((item) => item.recommended)?.id ?? null,
        })
        setMessages((current) => {
          const base = current.length > 0 ? current : conversation.data?.messages ?? []
          const withoutStub = base.filter((item) => item.id !== result.messageId)
          return [...withoutStub, resultMessage(result)]
        })
      } else {
        setPendingPlanChoice(null)
        setMessages((current) => {
          const base = current.length > 0 ? current : conversation.data?.messages ?? []
          // Replace awaiting stub if present (normal complete after selection uses same path).
          const withoutStub = base.filter((item) => item.id !== result.messageId)
          return [...withoutStub, resultMessage(result)]
        })
      }
      if (!conversationId) {
        navigate(`/chat/${result.conversationId}`, { replace: true })
      }
    } catch {
      setFailedQuestion(normalized)
      clearThinkingState()
    }
  }

  async function selectPlanOption(optionId: string) {
    if (!conversationId || sendMutation.isPending) return
    setSelectingOptionId(optionId)
    setFailedQuestion(null)
    clearThinkingState()
    // Seed plan checklist from chosen option so UI shows CoT/ToT execute immediately.
    const chosen = pendingPlanChoice?.options.find((item) => item.id === optionId)
    if (chosen) {
      setThinkingPlan({
        complexity: 'multi_step',
        difficulty: 'branched',
        reasoningMode: 'tot_select',
        planSource: 'user_selected',
        steps: chosen.steps.map((step) => ({ ...step, status: step.status || 'pending' })),
      })
    }
    try {
      const result = await sendMutation.mutateAsync({
        conversationId,
        knowledgeBaseIds: selectedKnowledgeBases,
        queryMode,
        selectedOptionId: optionId,
        streamHandlers: applyStreamHandlers(),
      })
      clearThinkingState()
      setPendingPlanChoice(null)
      setMessages((current) => {
        const base = current.length > 0 ? current : conversation.data?.messages ?? []
        const withoutStub = base.filter(
          (item) =>
            !(
              item.role === 'assistant' &&
              item.metadata.turnStatus === 'awaiting_plan_selection'
            ) && item.id !== result.messageId,
        )
        return [...withoutStub, resultMessage(result)]
      })
    } catch (error) {
      message.error(error instanceof Error ? error.message : '路径执行失败')
      clearThinkingState()
    } finally {
      setSelectingOptionId(null)
    }
  }

  async function cancelPendingPlan() {
    if (!conversationId || sendMutation.isPending) return
    try {
      const result = await sendMutation.mutateAsync({
        conversationId,
        knowledgeBaseIds: selectedKnowledgeBases,
        queryMode,
        cancelPendingPlan: true,
        streamHandlers: applyStreamHandlers(),
      })
      setPendingPlanChoice(null)
      clearThinkingState()
      setMessages((current) => {
        const base = current.length > 0 ? current : conversation.data?.messages ?? []
        const withoutStub = base.filter(
          (item) =>
            !(
              item.role === 'assistant' &&
              item.metadata.turnStatus === 'awaiting_plan_selection'
            ) && item.id !== result.messageId,
        )
        return [...withoutStub, resultMessage(result)]
      })
      message.success('已取消路径选择')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '取消失败')
    }
  }

  function openConversation(id: string) {
    if (id === conversationId) return
    navigate(`/chat/${id}`)
  }

  function startNewConversation() {
    navigate('/chat')
  }

  function openRename(item: ConversationSummary) {
    setRenaming(item)
    setRenameTitle(item.title)
  }

  async function confirmRename() {
    if (!renaming) return
    const nextTitle = renameTitle.trim()
    if (!nextTitle) {
      message.warning('会话标题不能为空')
      return
    }
    try {
      await renameMutation.mutateAsync({
        conversationId: renaming.id,
        title: nextTitle,
      })
      message.success('会话已重命名')
      setRenaming(null)
      setRenameTitle('')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '重命名失败')
    }
  }

  function confirmDelete(item: ConversationSummary) {
    modal.confirm({
      title: '删除历史会话',
      content: `确定删除“${item.title || '未命名会话'}”吗？删除后不会再出现在你的历史列表中。`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        await deleteMutation.mutateAsync(item.id)
        message.success('会话已删除')
        if (conversationId === item.id) {
          navigate('/chat')
        }
      },
    })
  }

  const noKnowledgeBases = knowledgeBases.isSuccess && knowledgeBases.data.length === 0
  const historyItems = history.data?.items ?? []
  const conversationTitle =
    conversation.data?.title ||
    historyItems.find((item) => item.id === conversationId)?.title ||
    (conversationId ? '会话' : '新会话')
  const selectedKbNames = (knowledgeBases.data ?? [])
    .filter((kb) => selectedKnowledgeBases.includes(kb.id))
    .map((kb) => kb.name)
  const scopeLabel =
    selectedKnowledgeBases.length === 0
      ? '全部授权知识库'
      : selectedKbNames.length > 0
        ? selectedKbNames.length <= 2
          ? selectedKbNames.join('、')
          : `${selectedKbNames.slice(0, 2).join('、')} 等 ${selectedKbNames.length} 个`
        : `${selectedKnowledgeBases.length} 个知识库`
  const modeLabel = queryModeLabels[queryMode] ?? queryMode

  return (
    <div className="chat-page">
      <div className="chat-page__body">
        <aside className="chat-page__history" aria-label="历史会话">
          <Card
            size="small"
            className="chat-page__history-card"
            title={
              <Space>
                <ClockCounterClockwise size={16} weight="duotone" />
                历史会话
              </Space>
            }
            extra={
              <Button
                type="primary"
                size="small"
                icon={<Plus size={16} weight="regular" />}
                onClick={startNewConversation}
              >
                新建
              </Button>
            }
            styles={{ body: { padding: 0 } }}
          >
            <div className="chat-history">
              <div className="chat-history__search">
                <Input
                  allowClear
                  value={historyKeyword}
                  onChange={(event) => setHistoryKeyword(event.target.value)}
                  placeholder="搜索标题或最近消息"
                  prefix={<MagnifyingGlass size={16} weight="regular" />}
                  aria-label="搜索历史会话" />
              </div>

              <button
                type="button"
                className={`chat-history__item chat-history__item--new${conversationId ? '' : ' is-active'}`}
                onClick={startNewConversation}
                aria-label="开始新会话"
                aria-current={conversationId ? undefined : 'page'}
              >
                <div className="chat-history__title">
                  <ChatCircle size={16} weight="duotone" />
                  新会话
                </div>
                <div className="chat-history__preview">从空白对话开始提问</div>
              </button>

              {history.isPending ? (
                <div className="chat-history__state">
                  <LoadingState message="正在加载历史会话…" minH="min-h-0" />
                </div>
              ) : history.isError ? (
                <div className="chat-history__state">
                  <Alert
                    type="error"
                    showIcon
                    message="历史会话加载失败"
                    action={
                      <Button size="small" onClick={() => void history.refetch()}>
                        重试
                      </Button>
                    } />
                </div>
              ) : historyItems.length === 0 ? (
                <div className="chat-history__state">
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description={
                      debouncedHistoryKeyword ? '没有匹配的历史会话' : '还没有历史会话'
                    } />
                </div>
              ) : (
                historyItems.map((item) => (
                  <HistoryItem
                    key={item.id}
                    item={item}
                    active={item.id === conversationId}
                    onOpen={() => openConversation(item.id)}
                    onRename={() => openRename(item)}
                    onDelete={() => confirmDelete(item)} />
                ))
              )}
            </div>
          </Card>
        </aside>

        <Card className="chat-page__main" styles={{ body: { padding: 0, height: '100%' } }}>
          <div className="chat-page__main-inner">
            <div className="chat-page__session-bar" aria-label="当前会话">
              <div className="chat-page__session-copy">
                <div className="chat-page__session-title" title={conversationTitle}>
                  <ChatCircle size={16} weight="duotone" aria-hidden />
                  <span>{conversationTitle}</span>
                </div>
                <div className="chat-page__session-meta">
                  <span className="chat-page__session-chip">
                    <BookOpen size={16} weight="duotone" aria-hidden />
                    {scopeLabel}
                  </span>
                  <span className="chat-page__session-dot" aria-hidden />
                  <span className="chat-page__session-chip">{modeLabel}</span>
                  {visibleMessages.length > 0 ? (
                    <>
                      <span className="chat-page__session-dot" aria-hidden />
                      <span className="chat-page__session-chip">
                        {visibleMessages.length} 条消息
                      </span>
                    </>
                  ) : null}
                </div>
              </div>
              {conversationId ? (
                <Button
                  type="text"
                  size="small"
                  icon={<PencilSimple size={16} weight="duotone" />}
                  onClick={() => {
                    const current = historyItems.find((item) => item.id === conversationId)
                    if (current) {
                      openRename(current)
                      return
                    }
                    openRename({
                      id: conversationId,
                      title: conversationTitle,
                      status: 'active',
                      messageCount: visibleMessages.length,
                      lastMessagePreview: null,
                      lastMessageRole: null,
                      createdAt: new Date().toISOString(),
                      updatedAt: new Date().toISOString(),
                    })
                  }}
                  aria-label="重命名会话"
                >
                  重命名
                </Button>
              ) : null}
            </div>
            <div
              aria-live="polite"
              className="chat-page__messages"
              ref={messagesContainerRef}
            >
              {conversation.isPending && conversationId && visibleMessages.length === 0 ? (
                <LoadingState message="正在加载会话…" minH="min-h-0" />
              ) : conversation.isError ? (
                <Alert
                  type="error"
                  showIcon
                  message="会话加载失败"
                  description={conversation.error.message}
                  action={
                    <Button size="small" onClick={() => void conversation.refetch()}>
                      重新加载
                    </Button>
                  } />
              ) : visibleMessages.length === 0 ? (
                <div className="chat-page__empty">
                  <Empty
                    image={
                      <div className="chat-page__empty-icon">
                        <Lightning size={16} weight="duotone" className="pf-brand-icon" />
                      </div>
                    }
                    description={
                      <div className="chat-page__empty-copy">
                        <Typography.Title level={5} style={{ marginBottom: 6 }}>
                          从一个制度问题开始
                        </Typography.Title>
                        <Typography.Text type="secondary">
                          例如：差旅住宿标准是多少？报销需要哪些附件？
                        </Typography.Text>
                        <div className="chat-page__suggestions">
                          {[
                            '差旅住宿标准是多少？',
                            '怎么报销？需要哪些材料？',
                            '请假流程有哪些步骤？',
                          ].map((sample) => (
                            <button
                              key={sample}
                              type="button"
                              className="chat-page__suggestion"
                              onClick={() => setQuestion(sample)}
                            >
                              {sample}
                            </button>
                          ))}
                        </div>
                      </div>
                    } />
                </div>
              ) : (
                visibleMessages.map((item) => (
                  <MessageCard
                    key={item.id}
                    message={item}
                    onCopy={() => void handleCopy(item.content)}
                    onEditQuestion={
                      item.role === 'user'
                        ? () => handleEditQuestion(item.content)
                        : undefined
                    }
                    onSelectPlanOption={
                      item.role === 'assistant' &&
                      item.metadata.turnStatus === 'awaiting_plan_selection'
                        ? (optionId) => void selectPlanOption(optionId)
                        : undefined
                    }
                    onCancelPendingPlan={
                      item.role === 'assistant' &&
                      item.metadata.turnStatus === 'awaiting_plan_selection'
                        ? () => void cancelPendingPlan()
                        : undefined
                    }
                    selectingOptionId={selectingOptionId}
                    selectionDisabled={sendMutation.isPending} />
                ))
              )}

              {sendMutation.isPending ? (
                <div className="chat-row chat-row--assistant">
                  <article
                    aria-label="PolicyFlow 思考中"
                    className="chat-bubble chat-bubble--assistant chat-bubble--thinking"
                  >
                    <div className="chat-bubble__title">
                      <Space size={8} wrap>
                        <Lightning size={16} weight="duotone" className="chat-thinking__pulse" style={{color: palette.primary}} aria-hidden />
                        <span>思考中</span>
                        {(() => {
                          const mode = describeExecutionMode(
                            thinkingPlan,
                            selectingOptionId ? 'tot_select' : pendingPlanChoice?.reasoningMode,
                            Boolean(selectingOptionId),
                          )
                          return (
                            <QuietChip tone={mode.tone} title={mode.detail}>
                              {mode.label}
                            </QuietChip>
                          )
                        })()}
                      </Space>
                    </div>
                    <ThinkingStreamPanel
                      stages={thinkingStages}
                      plan={thinkingPlan}
                      diagnostics={thinkingDiagnostics}
                      reasoningMode={
                        selectingOptionId
                          ? 'tot_select'
                          : pendingPlanChoice?.reasoningMode
                      }
                      executingSelection={Boolean(selectingOptionId)}
                      live />
                  </article>
                </div>
              ) : null}

              {/* Sticky picker only when history/local messages do not already embed the awaiting stub. */}
              {!sendMutation.isPending &&
              pendingPlanChoice &&
              pendingPlanChoice.options.length > 0 &&
              !visibleMessages.some(
                (item) =>
                  item.role === 'assistant' &&
                  item.metadata.turnStatus === 'awaiting_plan_selection' &&
                  (item.metadata.planOptions?.length ?? 0) > 0,
              ) ? (
                <div className="chat-row chat-row--assistant">
                  <article
                    aria-label="选择执行路径"
                    className="chat-bubble chat-bubble--assistant chat-bubble--thinking"
                  >
                    <div className="chat-bubble__title">
                      <Space size={8} wrap>
                        <Lightning size={16} weight="duotone" style={{ color: palette.primary }} aria-hidden />
                        <span>ToT 选路</span>
                        <QuietChip tone="accent" title="多候选计划 + 用户选路，非搜索式学术 ToT">
                          ToT 选路·待选择
                        </QuietChip>
                      </Space>
                    </div>
                    <PlanOptionPicker
                      options={pendingPlanChoice.options}
                      recommendedOptionId={pendingPlanChoice.recommendedOptionId}
                      loadingOptionId={selectingOptionId}
                      disabled={sendMutation.isPending}
                      onSelect={(optionId) => void selectPlanOption(optionId)}
                      onCancel={() => void cancelPendingPlan()} />
                  </article>
                </div>
              ) : null}

              {sendMutation.isError && failedQuestion ? (
                <Alert
                  type="error"
                  showIcon
                  message={sendMutation.error.message}
                  action={
                    <Button size="small" onClick={() => void submit(failedQuestion, false)}>
                      重试发送
                    </Button>
                  } />
              ) : null}
              <div ref={messagesEndRef} aria-hidden className="chat-page__messages-end" />
            </div>

            <div className="chat-page__composer">
              <form
                onSubmit={(event) => {
                  event.preventDefault()
                  void submit(question)
                }}
              >
                <div className="chat-page__composer-shell">
                  <label htmlFor="chat-question" className="chat-page__composer-label">
                    输入问题
                  </label>
                  <Input.TextArea
                    id="chat-question"
                    rows={3}
                    maxLength={4000}
                    value={question}
                    onChange={(event) => setQuestion(event.target.value)}
                    placeholder="描述制度场景，例如：差旅报销需要哪些附件？"
                    autoSize={{ minRows: 2, maxRows: 6 }}
                    onPressEnter={(event) => {
                      if (!event.shiftKey) {
                        event.preventDefault()
                        void submit(question)
                      }
                    }} />
                  <div className="chat-page__composer-actions">
                    <Typography.Text type="secondary" className="chat-page__composer-hint">
                      Enter 发送 · Shift + Enter 换行
                      {question.trim() ? ` · ${question.trim().length}/4000` : ''}
                    </Typography.Text>
                    <Button
                      type="primary"
                      htmlType="submit"
                      size="large"
                      loading={sendMutation.isPending}
                      disabled={!question.trim() || noKnowledgeBases}
                      className="chat-page__send"
                    >
                      <PaperPlaneTilt size={16} weight="duotone" aria-hidden />
                      发送
                    </Button>
                  </div>
                </div>
              </form>
            </div>
          </div>
        </Card>

        <aside className="chat-page__sidebar">
          <Card title="检索范围" size="small" className="chat-page__side-card">
            <Typography.Paragraph type="secondary" className="chat-page__side-help">
              不选择时，将检索你有权限的全部知识库。
            </Typography.Paragraph>
            {knowledgeBases.isPending ? (
              <Typography.Text type="secondary">正在加载知识库…</Typography.Text>
            ) : knowledgeBases.isError ? (
              <Alert
                type="error"
                showIcon
                message="知识库加载失败"
                action={
                  <Button size="small" onClick={() => void knowledgeBases.refetch()}>
                    重试
                  </Button>
                } />
            ) : noKnowledgeBases ? (
              <Typography.Text type="secondary">
                还没有可访问的知识库，请联系知识库管理员授权。
              </Typography.Text>
            ) : (
              <Checkbox.Group
                className="chat-page__kb-list"
                value={selectedKnowledgeBases}
                onChange={(values) => setSelectedKnowledgeBases(values as string[])}
                options={knowledgeBases.data.map((kb) => ({
                  label: (
                    <span className="chat-page__kb-option">
                      <span className="chat-page__kb-name">{kb.name}</span>
                      <Typography.Text type="secondary" className="chat-page__kb-code">
                        {kb.code}
                      </Typography.Text>
                    </span>
                  ),
                  value: kb.id,
                }))} />
            )}
          </Card>

          <Card title="检索模式" size="small" className="chat-page__side-card">
            <Select
              style={{ width: '100%' }}
              value={queryMode}
              onChange={(value) => setQueryMode(value as QueryMode)}
              options={queryModeOptions} />
            <Typography.Paragraph type="secondary" className="chat-page__side-help" style={{ marginTop: 10, marginBottom: 0 }}>
              默认混合模式更稳；需要更宽覆盖时可切换全局或混合增强。
            </Typography.Paragraph>
          </Card>
        </aside>
      </div>

      <Modal
        title="重命名会话"
        open={Boolean(renaming)}
        okText="保存"
        cancelText="取消"
        confirmLoading={renameMutation.isPending}
        onOk={() => void confirmRename()}
        onCancel={() => {
          setRenaming(null)
          setRenameTitle('')
        }}
        destroyOnHidden
      >
        <Input
          value={renameTitle}
          maxLength={255}
          onChange={(event) => setRenameTitle(event.target.value)}
          placeholder="输入新的会话标题"
          aria-label="会话标题"
          onPressEnter={() => void confirmRename()} />
      </Modal>
    </div>
  )
}

function HistoryItem({
  item,
  active,
  onOpen,
  onRename,
  onDelete,
}: {
  item: ConversationSummary
  active: boolean
  onOpen: () => void
  onRename: () => void
  onDelete: () => void
}) {
  return (
    <div className={`chat-history__item${active ? ' is-active' : ''}`}>
      <button
        type="button"
        className="chat-history__open"
        onClick={onOpen}
        aria-label={`打开会话：${item.title || '未命名会话'}`}
        aria-current={active ? 'page' : undefined}
      >
        <div className="chat-history__item-top">
          <div className="chat-history__title" title={item.title}>
            {item.title || '未命名会话'}
          </div>
          <span className="chat-history__time">{formatHistoryTime(item.updatedAt)}</span>
        </div>
        <div className="chat-history__preview">
          {item.lastMessagePreview || '暂无消息摘要'}
        </div>
        <div className="chat-history__meta">{item.messageCount} 条消息</div>
      </button>
      <div className="chat-history__actions">
        <button
          type="button"
          className="chat-history__action"
          aria-label={`重命名会话：${item.title || '未命名会话'}`}
          onClick={(event) => {
            event.stopPropagation()
            onRename()
          }}
        >
          <PencilSimple size={16} weight="duotone" />
        </button>
        <button
          type="button"
          className="chat-history__action chat-history__action--danger"
          aria-label={`删除会话：${item.title || '未命名会话'}`}
          onClick={(event) => {
            event.stopPropagation()
            onDelete()
          }}
        >
          <Trash size={16} weight="duotone" />
        </button>
      </div>
    </div>
  )
}

function MessageCard({
  message,
  onCopy,
  onEditQuestion,
  onSelectPlanOption,
  onCancelPendingPlan,
  selectingOptionId,
  selectionDisabled,
}: {
  message: ConversationMessage
  onCopy: () => void
  onEditQuestion?: () => void
  onSelectPlanOption?: (optionId: string) => void
  onCancelPendingPlan?: () => void
  selectingOptionId?: string | null
  selectionDisabled?: boolean
}) {
  if (message.role === 'user') {
    return (
      <div className="chat-row chat-row--user">
        <div className="chat-bubble chat-bubble--user">
          <div className="chat-bubble__content">{message.content}</div>
          <div className="chat-bubble__actions chat-bubble__actions--user">
            <Button
              type="text"
              size="small"
              icon={<Copy size={16} weight="duotone" />}
              aria-label="复制问题"
              onClick={onCopy}
            >
              复制
            </Button>
            {onEditQuestion ? (
              <Button
                type="text"
                size="small"
                icon={<PencilSimple size={16} weight="duotone" />}
                aria-label="编辑问题"
                onClick={onEditQuestion}
              >
                编辑
              </Button>
            ) : null}
          </div>
        </div>
      </div>
    )
  }

  const isAwaiting =
    message.metadata.turnStatus === 'awaiting_plan_selection' &&
    (message.metadata.planOptions?.length ?? 0) > 0
  const noEvidence =
    !isAwaiting &&
    message.metadata.citations.length === 0 &&
    (message.metadata.confidenceScore === 0 ||
      message.metadata.compliance?.warnings?.includes('NO_RELIABLE_EVIDENCE') === true)
  const reasoningBadge = describeReasoningBadge(
    message.metadata.reasoningMode,
    message.metadata.routerResult?.difficulty,
    message.metadata.turnStatus,
  )

  return (
    <div className="chat-row chat-row--assistant">
      <article
        aria-label={isAwaiting ? '选择执行路径' : 'PolicyFlow 回答'}
        className={`chat-bubble chat-bubble--assistant${noEvidence ? ' chat-bubble--warning' : ''}${
          isAwaiting ? ' chat-bubble--thinking' : ''
        }`}
      >
        <div className="chat-bubble__title">
          <Space size={8} wrap>
            <Lightning size={16} weight="duotone" style={{color: noEvidence ? palette.warning : palette.primary}} aria-hidden />
            <span>{isAwaiting ? 'ToT 选路' : 'PolicyFlow 回答'}</span>
            {reasoningBadge ? (
              <QuietChip tone={reasoningBadge.tone} title={reasoningBadge.detail}>
                {reasoningBadge.label}
              </QuietChip>
            ) : null}
            {noEvidence ? <QuietChip tone="warning">模型参考 · 不可信</QuietChip> : null}
          </Space>
        </div>

        {noEvidence ? (
          <Alert
            type="warning"
            showIcon
            icon={<Warning size={16} weight="duotone" />}
            style={{ marginBottom: 12 }}
            message="未找到可靠依据"
            description="当前授权知识库没有检索到制度证据。下面是模型基于通用知识给出的参考回答，不能作为正式制度依据，请你自行判断，并与相关部门确认后再执行。" />
        ) : null}

        <MarkdownContent
          className="chat-bubble__content chat-bubble__content--assistant"
          content={message.content} />

        {isAwaiting && onSelectPlanOption ? (
          <PlanOptionPicker
            options={message.metadata.planOptions ?? []}
            recommendedOptionId={
              message.metadata.planOptions?.find((item) => item.recommended)?.id ?? null
            }
            loadingOptionId={selectingOptionId ?? null}
            disabled={Boolean(selectionDisabled)}
            onSelect={onSelectPlanOption}
            onCancel={onCancelPendingPlan} />
        ) : null}

        {noEvidence || isAwaiting ? null : <CitationList citations={message.metadata.citations} />}

        {!isAwaiting ? (
          <CompletedTurnTrace diagnostics={message.metadata.diagnostics} />
        ) : null}

        {!isAwaiting ? (
          <div className="chat-bubble__meta">
            {noEvidence ? (
              <QuietChip tone="warning">可信度 0% · 需人工判断</QuietChip>
            ) : message.metadata.confidenceScore !== null ? (
              <QuietChip>
                可信度 {Math.round(message.metadata.confidenceScore * 100)}%
              </QuietChip>
            ) : null}
            {message.metadata.queryMode ? (
              <QuietChip>
                {queryModeLabels[message.metadata.queryMode] ?? message.metadata.queryMode}
              </QuietChip>
            ) : null}
            {message.metadata.compliance?.passed ? (
              <QuietChip tone="success">
                <CheckCircle size={12} weight="duotone" aria-hidden />
                合规通过
              </QuietChip>
            ) : null}
          </div>
        ) : null}

        {!isAwaiting && message.metadata.suggestedSkills.length > 0 ? (
          <div className="chat-bubble__skills">
            <div className="chat-bubble__skills-title">建议能力</div>
            {message.metadata.suggestedSkills.map((skill) => (
              <div key={skill.name} style={{ fontSize: 12, marginBottom: 4 }}>
                {skill.name}：{skill.description}
              </div>
            ))}
          </div>
        ) : null}

        {!isAwaiting && message.metadata.queryLogId ? (
          <FeedbackActions queryLogId={message.metadata.queryLogId} />
        ) : !isAwaiting ? (
          <Typography.Text
            type="secondary"
            style={{ display: 'block', marginTop: 12, fontSize: 12 }}
          >
            当前回答缺少反馈标识，暂不能评价。
          </Typography.Text>
        ) : null}

        <div className="chat-bubble__actions chat-bubble__actions--footer">
          <Button
            type="text"
            size="small"
            icon={<Copy size={16} weight="duotone" />}
            aria-label="复制回答"
            onClick={onCopy}
          >
            复制
          </Button>
        </div>
      </article>
    </div>
  )
}

function CitationList({ citations }: { citations: AssistantMetadata['citations'] }) {
  if (citations.length === 0) return null
  return (
    <details className="chat-citations">
      <summary>查看引用（{citations.length}）</summary>
      <div className="chat-citations__list">
        {citations.map((citation, index) => (
          <div
            key={`${citation.documentId ?? citation.knowledgeBaseId}-${index}`}
            className="chat-citations__item"
          >
            <div className="chat-citations__item-head">
              <BookOpen size={16} weight="duotone" className="pf-brand-icon" />
              <strong>
                {citation.knowledgeBaseName} · {citation.documentTitle ?? '未命名文档'}
              </strong>
            </div>
            <p className="chat-citations__snippet">{citation.snippet}</p>
            {citation.chunkId ? (
              <p className="chat-citations__chunk">Chunk：{citation.chunkId}</p>
            ) : null}
          </div>
        ))}
      </div>
    </details>
  )
}

const memoryTypeLabel: Record<string, string> = {
  user_preference: '偏好',
  long_term_event: '长期',
  entity: '实体',
  stm_summary: '摘要',
  conversation_summary: '会话',
  conversation_history: '历史',
}

const sourceSlotLabel: Record<string, string> = {
  fixed: '固定加载',
  recalled: '按需召回',
  rolling_summary: '滚动摘要',
  history: '短期窗口',
}

function prettyJson(value: Record<string, unknown>): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

type ChipTone = 'neutral' | 'active' | 'success' | 'warning' | 'error' | 'accent'

function QuietChip({
  children,
  tone = 'neutral',
  title,
}: {
  children: ReactNode
  tone?: ChipTone
  title?: string
}) {
  return (
    <span className={`chat-chip chat-chip--${tone}`} title={title}>
      {children}
    </span>
  )
}

function statusTone(status: string): ChipTone {
  if (status === 'running' || status === 'processing') return 'active'
  if (status === 'success' || status === 'suggested') return 'success'
  if (status === 'warning') return 'warning'
  if (status === 'error' || status === 'failed') return 'error'
  return 'neutral'
}

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    pending: '待执行',
    running: '进行中',
    success: '完成',
    skipped: '跳过',
    empty: '空',
    warning: '警告',
    error: '失败',
    suggested: '建议',
  }
  return map[status] ?? status
}

function describeReasoningBadge(
  reasoningMode?: ReasoningMode | null,
  difficulty?: string | null,
  turnStatus?: string | null,
): { label: string; tone: ChipTone; detail: string } | null {
  if (turnStatus === 'awaiting_plan_selection' || reasoningMode === 'tot_select') {
    return {
      label: turnStatus === 'awaiting_plan_selection' ? 'ToT 选路·待选择' : 'ToT 选路',
      tone: 'accent',
      detail: '多候选计划 + 用户选路，非搜索式学术 ToT',
    }
  }
  if (reasoningMode === 'cot_steps' || difficulty === 'multi_step') {
    return {
      label: 'CoT 分步',
      tone: 'active',
      detail: '单链分步计划（Plan-and-Execute）',
    }
  }
  if (reasoningMode === 'cot_direct' || difficulty === 'simple') {
    return {
      label: 'CoT 直答',
      tone: 'neutral',
      detail: '简单问答直答路径',
    }
  }
  return null
}

function describeExecutionMode(
  plan: ChatPlanEvent | null,
  reasoningMode?: ReasoningMode | null,
  executingSelection = false,
): {
  label: string
  tone: ChipTone
  detail: string
} {
  const mode = reasoningMode || plan?.reasoningMode
  const difficulty = plan?.difficulty

  if (executingSelection || (mode === 'tot_select' && plan?.planSource === 'user_selected')) {
    const steps = plan?.steps ?? []
    const waves =
      plan?.waves && plan.waves.length > 0 ? plan.waves : inferWavesFromSteps(steps)
    const hasParallelWave = waves.some((wave) => wave.length > 1)
    if (hasParallelWave || plan?.parallelUsed) {
      return {
        label: 'ToT 选路·并行执行',
        tone: 'accent',
        detail: '用户已选路径，按依赖波次并行执行（非学术 ToT 搜索）',
      }
    }
    return {
      label: 'ToT 选路·执行中',
      tone: 'accent',
      detail: '用户已选路径，按选定步骤执行（非学术 ToT 搜索）',
    }
  }

  if (mode === 'tot_select' || difficulty === 'branched') {
    return {
      label: 'ToT 选路·待选择',
      tone: 'accent',
      detail: '多候选计划 + 用户选路，非搜索式学术 ToT',
    }
  }

  if (!plan || !plan.steps?.length) {
    if (mode === 'cot_direct') {
      return {
        label: 'CoT 直答',
        tone: 'neutral',
        detail: '简单问答直答路径',
      }
    }
    return {
      label: '流式阶段',
      tone: 'active',
      detail: '记忆 → 路由 → 检索 → 回答',
    }
  }

  const steps = plan.steps
  const waves =
    plan.waves && plan.waves.length > 0
      ? plan.waves
      : inferWavesFromSteps(steps)
  const hasParallelWave = waves.some((wave) => wave.length > 1)
  const parallelLive = Boolean(plan.parallelUsed) || hasParallelWave
  const running = steps.filter((s) => s.status === 'running')
  const concurrentRunning = running.length >= 2
  const isL2 = plan.executor === 'L2' || Boolean(plan.parallelUsed) || hasParallelWave

  if (parallelLive || concurrentRunning) {
    const parallelWaveSize = Math.max(
      ...waves.map((w) => w.length),
      concurrentRunning ? running.length : 1,
    )
    return {
      label: concurrentRunning ? 'CoT 分步·并行中' : 'CoT 分步·并行',
      tone: 'active',
      detail:
        parallelWaveSize > 1
          ? `独立子任务可同波并行（最大 ${parallelWaveSize} 路）`
          : '独立子任务可并行',
    }
  }

  if (isL2 || plan.complexity === 'multi_step' || mode === 'cot_steps') {
    return {
      label: 'CoT 分步',
      tone: 'active',
      detail: `按依赖顺序执行 ${steps.length} 步`,
    }
  }

  return {
    label: 'CoT 直答',
    tone: 'neutral',
    detail: '记忆 → 路由 → 检索 → 回答',
  }
}

function inferWavesFromSteps(steps: PlanStep[]): string[][] {
  // Client-side fallback when backend has not yet sent waves:
  // independent ready retrieve/skill steps share a wave; remaining steps stay serial.
  const byId = new Map(steps.map((s) => [s.id, s]))
  const ready = steps.filter((s) => {
    const deps = s.dependsOn ?? []
    if (deps.length === 0) return true
    return deps.every((d) => {
      const dep = byId.get(d)
      return dep && (dep.status === 'success' || dep.status === 'skipped')
    })
  })
  const parallelizable = ready.filter(
    (s) =>
      (s.kind === 'retrieve' || s.kind === 'skill') &&
      (s.status === 'pending' || s.status === 'running'),
  )
  if (parallelizable.length < 2) {
    return steps.map((s) => [s.id])
  }

  const parallelIds = new Set(parallelizable.map((s) => s.id))
  const waves: string[][] = [parallelizable.map((s) => s.id)]
  for (const step of steps) {
    if (!parallelIds.has(step.id)) {
      waves.push([step.id])
    }
  }
  return waves
}

function PlanOptionPicker({
  options,
  recommendedOptionId,
  loadingOptionId,
  disabled,
  onSelect,
  onCancel,
}: {
  options: PlanOption[]
  recommendedOptionId?: string | null
  loadingOptionId?: string | null
  disabled?: boolean
  onSelect: (optionId: string) => void
  onCancel?: () => void
}) {
  if (!options.length) return null
  return (
    <div className="chat-thinking__options" aria-label="候选执行路径">
      <div className="chat-thinking__options-title">
        <strong>请选择一条执行路径</strong>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          多候选计划 + 用户选路（非搜索式学术 ToT）
        </Typography.Text>
      </div>
      <div className="chat-thinking__option-list">
        {options.map((option) => {
          const recommended =
            option.recommended || option.id === recommendedOptionId
          return (
            <div
              key={option.id}
              className={`chat-thinking__option-card${
                recommended ? ' chat-thinking__option-card--recommended' : ''
              }`}
            >
              <div className="chat-thinking__option-head">
                <strong>{option.title}</strong>
                {recommended ? <QuietChip tone="accent">推荐</QuietChip> : null}
                <QuietChip>{option.steps.length} 步</QuietChip>
              </div>
              {option.summary ? (
                <Typography.Paragraph
                  type="secondary"
                  style={{ marginBottom: 8, fontSize: 13 }}
                >
                  {option.summary}
                </Typography.Paragraph>
              ) : null}
              {option.tradeoffs.length > 0 ? (
                <ul className="chat-thinking__option-tradeoffs">
                  {option.tradeoffs.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              ) : null}
              {option.steps.length > 0 ? (
                <details className="chat-thinking__option-steps-wrap">
                  <summary>步骤预览 · {option.steps.length}</summary>
                  <ol className="chat-thinking__option-steps">
                    {option.steps.map((step, index) => (
                      <li key={step.id}>
                        <span className="chat-thinking__plan-index">{index + 1}</span>
                        <span>{step.title}</span>
                        {step.kind ? <QuietChip>{step.kind}</QuietChip> : null}
                      </li>
                    ))}
                  </ol>
                </details>
              ) : null}
              <div className="chat-thinking__option-actions">
                <Button
                  type={recommended ? 'primary' : 'default'}
                  size="small"
                  loading={loadingOptionId === option.id}
                  disabled={Boolean(disabled) || Boolean(loadingOptionId)}
                  onClick={() => onSelect(option.id)}
                >
                  选择此路径
                </Button>
              </div>
            </div>
          )
        })}
      </div>
      {onCancel ? (
        <div className="chat-thinking__options-footer">
          <Button
            type="text"
            size="small"
            danger
            disabled={Boolean(disabled) || Boolean(loadingOptionId)}
            onClick={onCancel}
          >
            取消选路
          </Button>
        </div>
      ) : null}
    </div>
  )
}

function PlanStepRow({
  step,
  index,
  parallel = false,
}: {
  step: PlanStep
  index: number
  parallel?: boolean
}) {
  const isActive = step.status === 'running' || step.status === 'error'
  const [open, setOpen] = useState(isActive)

  useEffect(() => {
    if (isActive) setOpen(true)
    else if (step.status === 'success' || step.status === 'skipped') setOpen(false)
  }, [isActive, step.status])

  return (
    <details
      className={`chat-thinking__plan-item chat-thinking__plan-item--${step.status || 'pending'}${
        parallel ? ' chat-thinking__plan-item--parallel' : ''
      }`}
      open={open}
      onToggle={(event) => setOpen((event.currentTarget as HTMLDetailsElement).open)}
    >
      <summary className="chat-thinking__plan-item-head">
        <span className="chat-thinking__plan-index">{index}</span>
        <strong>{step.title}</strong>
        {step.kind ? <QuietChip>{step.kind}</QuietChip> : null}
        {parallel ? <QuietChip tone="active">并行</QuietChip> : null}
        <QuietChip tone={statusTone(String(step.status))}>
          {statusLabel(String(step.status))}
        </QuietChip>
      </summary>
      {step.dependsOn && step.dependsOn.length > 0 ? (
        <div className="chat-thinking__plan-message">依赖：{step.dependsOn.join(', ')}</div>
      ) : null}
      {step.message ? (
        <div className="chat-thinking__plan-message">{step.message}</div>
      ) : null}
    </details>
  )
}

function PlanWaveGroup({
  waveIndex,
  parallel,
  parallelCount = 1,
  waveRunning,
  waveDone,
  children,
}: {
  waveIndex: number
  parallel: boolean
  parallelCount?: number
  waveRunning: boolean
  waveDone: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(waveRunning || !waveDone)

  useEffect(() => {
    if (waveRunning) setOpen(true)
    else if (waveDone) setOpen(false)
  }, [waveRunning, waveDone])

  return (
    <details
      className={
        parallel
          ? 'chat-thinking__wave chat-thinking__wave--parallel'
          : 'chat-thinking__wave'
      }
      open={open}
      onToggle={(event) => setOpen((event.currentTarget as HTMLDetailsElement).open)}
    >
      <summary className="chat-thinking__wave-head">
        <span>第 {waveIndex + 1} 波</span>
        <QuietChip tone={parallel ? 'active' : 'neutral'}>
          {parallel ? `并行 ×${parallelCount}` : '串行'}
        </QuietChip>
        <QuietChip tone={waveRunning ? 'active' : waveDone ? 'success' : 'neutral'}>
          {waveRunning ? '进行中' : waveDone ? '完成' : '待执行'}
        </QuietChip>
      </summary>
      {children}
    </details>
  )
}

function ThinkingStreamPanel({
  stages,
  plan,
  diagnostics,
  reasoningMode,
  executingSelection = false,
  live = false,
}: {
  stages: ChatStageEvent[]
  plan: ChatPlanEvent | null
  diagnostics: TurnDiagnostics
  reasoningMode?: ReasoningMode | null
  executingSelection?: boolean
  live?: boolean
}) {
  const memoryCount = diagnostics.memories.length
  const toolCount = diagnostics.tools.length
  const commandCount = Math.max(diagnostics.commands.length, stages.length)
  const planSteps = plan?.steps ?? []
  const mode = describeExecutionMode(plan, reasoningMode, executingSelection)
  const waves =
    plan?.waves && plan.waves.length > 0
      ? plan.waves
      : planSteps.length > 0
        ? inferWavesFromSteps(planSteps)
        : []
  const stepById = new Map(planSteps.map((s) => [s.id, s]))
  const parallelWaveIds = new Set(
    waves.filter((w) => w.length > 1).flatMap((w) => w),
  )
  const visibleStages =
    stages.length > 0
      ? stages
      : [{ stage: '准备中', status: 'running', message: '正在初始化本轮执行…' }]
  const latestStage = visibleStages[visibleStages.length - 1]
  const completedStages = visibleStages.slice(0, -1).filter((item) => item.status !== 'running')
  const donePlanCount = planSteps.filter(
    (step) => step.status === 'success' || step.status === 'skipped',
  ).length
  const runningPlan = planSteps.find((step) => step.status === 'running')

  return (
    <div className={`chat-thinking${live ? ' chat-thinking--live' : ''}`} aria-live="polite">
      <div className="chat-thinking__status-line">
        <span className="chat-thinking__status-dot" aria-hidden />
        <span className="chat-thinking__status-text">
          {runningPlan
            ? `正在执行：${runningPlan.title}`
            : latestStage?.message || latestStage?.stage || '处理中…'}
        </span>
        <QuietChip tone={mode.tone} title={mode.detail}>
          {mode.label}
        </QuietChip>
      </div>

      <div className="chat-diagnostics__overview">
        <span className="chat-diagnostics__overview-label">本轮使用</span>
        <QuietChip>记忆 {memoryCount}</QuietChip>
        <QuietChip>工具 {toolCount}</QuietChip>
        <QuietChip>命令 {commandCount}</QuietChip>
        {planSteps.length > 0 ? (
          <QuietChip>
            计划 {donePlanCount}/{planSteps.length}
            {plan?.planSource ? ` · ${plan.planSource}` : ''}
          </QuietChip>
        ) : null}
      </div>

      {planSteps.length > 0 ? (
        <details className="chat-thinking__plan" aria-label="执行计划" open>
          <summary className="chat-thinking__plan-title">
            <strong>执行计划</strong>
            <QuietChip tone={mode.tone}>{mode.label}</QuietChip>
            {plan?.executor ? <QuietChip>{plan.executor}</QuietChip> : null}
            <span className="chat-thinking__hint">
              {donePlanCount}/{planSteps.length} 完成 · {mode.detail}
            </span>
          </summary>

          {waves.some((w) => w.length > 1) ? (
            <div className="chat-thinking__waves" aria-label="执行波次">
              {waves.map((wave, waveIndex) => {
                const parallel = wave.length > 1
                const waveSteps = wave
                  .map((id) => stepById.get(id))
                  .filter((step): step is PlanStep => Boolean(step))
                const waveDone = waveSteps.every(
                  (step) => step.status === 'success' || step.status === 'skipped',
                )
                const waveRunning = waveSteps.some((step) => step.status === 'running')
                return (
                  <PlanWaveGroup
                    key={`wave-${waveIndex}`}
                    waveIndex={waveIndex}
                    parallel={parallel}
                    parallelCount={wave.length}
                    waveRunning={waveRunning}
                    waveDone={waveDone}
                  >
                    <div className="chat-thinking__plan-list">
                      {wave.map((id) => {
                        const step = stepById.get(id)
                        if (!step) return null
                        const index = planSteps.findIndex((s) => s.id === id)
                        return (
                          <PlanStepRow
                            key={step.id}
                            step={step}
                            index={index >= 0 ? index + 1 : 0}
                            parallel={parallelWaveIds.has(step.id)}
                          />
                        )
                      })}
                    </div>
                  </PlanWaveGroup>
                )
              })}
            </div>
          ) : (
            <div className="chat-thinking__plan-list">
              {planSteps.map((step: PlanStep, index: number) => (
                <PlanStepRow
                  key={step.id}
                  step={step}
                  index={index + 1}
                />
              ))}
            </div>
          )}
        </details>
      ) : null}

      <div className="chat-thinking__timeline" aria-label="执行阶段">
        {completedStages.length > 0 ? (
          <details className="chat-thinking__completed">
            <summary>
              已完成阶段 · {completedStages.length}
              <span className="chat-thinking__hint">
                {completedStages
                  .slice(-3)
                  .map((item) => item.stage)
                  .join(' → ')}
              </span>
            </summary>
            <div className="chat-thinking__stages">
              {completedStages.map((stage) => (
                <div
                  key={stage.stage}
                  className={`chat-thinking__stage chat-thinking__stage--${stage.status}`}
                >
                  <div className="chat-thinking__stage-head">
                    <strong>{stage.stage}</strong>
                    <QuietChip tone={statusTone(stage.status)}>
                      {statusLabel(stage.status)}
                    </QuietChip>
                  </div>
                  <div className="chat-thinking__stage-message">{stage.message}</div>
                </div>
              ))}
            </div>
          </details>
        ) : null}

        {latestStage ? (
          <div
            className={`chat-thinking__stage chat-thinking__stage--current chat-thinking__stage--${latestStage.status}`}
          >
            <div className="chat-thinking__stage-head">
              <strong>{latestStage.stage}</strong>
              <QuietChip tone={statusTone(latestStage.status)}>
                {statusLabel(latestStage.status)}
              </QuietChip>
            </div>
            <div className="chat-thinking__stage-message">{latestStage.message}</div>
          </div>
        ) : null}
      </div>

      {memoryCount + toolCount + diagnostics.commands.length > 0 ? (
        <TurnDiagnosticsPanel diagnostics={diagnostics} compact defaultOpen={false} />
      ) : (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          记忆 / 工具 / 命令会随步骤结果逐步写入，完成后可折叠查看。
        </Typography.Text>
      )}
    </div>
  )
}

function CompletedTurnTrace({ diagnostics }: { diagnostics?: TurnDiagnostics | null }) {
  const data = diagnostics ?? { memories: [], tools: [], commands: [] }
  const total = data.memories.length + data.tools.length + data.commands.length
  if (total === 0) return null

  return (
    <details className="chat-turn-trace" aria-label="本轮执行过程">
      <summary>
        <span>执行过程</span>
        <span className="chat-turn-trace__summary">
          记忆 {data.memories.length} · 工具 {data.tools.length} · 命令 {data.commands.length}
        </span>
      </summary>
      <TurnDiagnosticsPanel diagnostics={data} defaultOpen />
    </details>
  )
}

function TurnDiagnosticsPanel({
  diagnostics,
  compact = false,
  defaultOpen = false,
}: {
  diagnostics?: TurnDiagnostics | null
  compact?: boolean
  defaultOpen?: boolean
}) {
  const data = diagnostics ?? { memories: [], tools: [], commands: [] }
  const memoryCount = data.memories.length
  const toolCount = data.tools.length
  const commandCount = data.commands.length
  if (memoryCount + toolCount + commandCount === 0) return null

  return (
    <div className="chat-diagnostics" aria-label="本轮执行摘要">
      {compact ? null : (
        <div className="chat-diagnostics__overview">
          <span className="chat-diagnostics__overview-label">本轮使用</span>
          <QuietChip>记忆 {memoryCount}</QuietChip>
          <QuietChip>工具 {toolCount}</QuietChip>
          <QuietChip>命令 {commandCount}</QuietChip>
        </div>
      )}

      {memoryCount > 0 ? (
        <details className="chat-diagnostics__section" open={defaultOpen && !compact}>
          <summary>
            记忆 · {memoryCount}
            <span className="chat-diagnostics__hint">
              {data.memories
                .slice(0, 3)
                .map((item) => memoryTypeLabel[item.memoryType] ?? item.memoryType)
                .join(' / ')}
            </span>
          </summary>
          <div className="chat-diagnostics__body">
            {data.memories.map((item, index) => (
              <MemoryDiagItem key={`${item.id ?? item.memoryType}-${index}`} item={item} />
            ))}
          </div>
        </details>
      ) : null}

      {toolCount > 0 ? (
        <details className="chat-diagnostics__section" open={defaultOpen && !compact}>
          <summary>
            工具 · {toolCount}
            <span className="chat-diagnostics__hint">
              {data.tools
                .slice(0, 3)
                .map((item) => item.toolName)
                .join(' / ')}
            </span>
          </summary>
          <div className="chat-diagnostics__body">
            {data.tools.map((item, index) => (
              <ToolDiagItem key={`${item.toolName}-${index}`} item={item} />
            ))}
          </div>
        </details>
      ) : null}

      {commandCount > 0 ? (
        <details className="chat-diagnostics__section" open={defaultOpen && !compact}>
          <summary>
            命令 · {commandCount}
            <span className="chat-diagnostics__hint">
              {data.commands
                .slice(0, 4)
                .map((item) => item.name)
                .join(' → ')}
            </span>
          </summary>
          <div className="chat-diagnostics__body">
            {data.commands.map((item, index) => (
              <CommandDiagItem key={`${item.name}-${index}`} item={item} />
            ))}
          </div>
        </details>
      ) : null}
    </div>
  )
}

function MemoryDiagItem({ item }: { item: UsedMemoryItem }) {
  return (
    <div className="chat-diagnostics__item">
      <div className="chat-diagnostics__item-head">
        <QuietChip>{memoryTypeLabel[item.memoryType] ?? item.memoryType}</QuietChip>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {sourceSlotLabel[item.sourceSlot] ?? item.sourceSlot}
          {item.confidence !== null ? ` · conf ${item.confidence.toFixed(2)}` : ''}
        </Typography.Text>
      </div>
      <div className="chat-diagnostics__item-content">{item.content}</div>
    </div>
  )
}

function ToolDiagItem({ item }: { item: ToolCallTrace }) {
  const hasPayload =
    Object.keys(item.inputSummary).length > 0 ||
    Object.keys(item.outputSummary).length > 0 ||
    Boolean(item.errorMessage)
  return (
    <div className="chat-diagnostics__item">
      <div className="chat-diagnostics__item-head">
        <strong>{item.toolName}</strong>
        <QuietChip tone={statusTone(item.status)}>{statusLabel(item.status)}</QuietChip>
        {item.agentName ? (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            via {item.agentName}
          </Typography.Text>
        ) : null}
        {item.latencyMs > 0 ? (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {item.latencyMs} ms
          </Typography.Text>
        ) : null}
      </div>
      {hasPayload ? (
        <details className="chat-diagnostics__nested">
          <summary>输入 / 输出</summary>
          {Object.keys(item.inputSummary).length > 0 ? (
            <pre className="chat-diagnostics__code">{prettyJson(item.inputSummary)}</pre>
          ) : null}
          {Object.keys(item.outputSummary).length > 0 ? (
            <pre className="chat-diagnostics__code">{prettyJson(item.outputSummary)}</pre>
          ) : null}
          {item.errorMessage ? (
            <Typography.Text type="danger" style={{ fontSize: 12 }}>
              {item.errorMessage}
            </Typography.Text>
          ) : null}
        </details>
      ) : null}
    </div>
  )
}

function CommandDiagItem({ item }: { item: CommandTrace }) {
  const hasOutput = Object.keys(item.output).length > 0
  return (
    <div className="chat-diagnostics__item">
      <div className="chat-diagnostics__item-head">
        <strong>{item.name}</strong>
        <QuietChip tone={statusTone(item.status)}>{statusLabel(item.status)}</QuietChip>
      </div>
      {item.summary ? <div className="chat-diagnostics__item-content">{item.summary}</div> : null}
      {hasOutput ? (
        <details className="chat-diagnostics__nested">
          <summary>输出结果</summary>
          <pre className="chat-diagnostics__code">{prettyJson(item.output)}</pre>
        </details>
      ) : null}
    </div>
  )
}

const feedbackOptions: Array<{ value: FeedbackRating; label: string }> = [
  { value: 'useful', label: '有帮助' },
  { value: 'not_useful', label: '没有帮助' },
  { value: 'wrong_citation', label: '引用错误' },
  { value: 'incomplete', label: '回答不完整' },
]

function FeedbackActions({ queryLogId }: { queryLogId: string }) {
  const mutation = useFeedbackMutation(queryLogId)
  const [rating, setRating] = useState<FeedbackRating>('useful')
  const [comment, setComment] = useState('')
  const [statusMessage, setStatusMessage] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const selectedLabel = useMemo(
    () => feedbackOptions.find((option) => option.value === rating)?.label,
    [rating],
  )

  async function submitFeedbackNow() {
    setStatusMessage(null)
    setErrorMessage(null)
    try {
      await mutation.mutateAsync({ rating, comment })
      setStatusMessage(`已记录“${selectedLabel}”，再次提交会覆盖你的上一条反馈。`)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '反馈提交失败')
    }
  }

  return (
    <div className="chat-feedback">
      <div className="chat-feedback__row">
        <label className="chat-feedback__field">
          <span>评价</span>
          <select
            aria-label="回答评价"
            value={rating}
            onChange={(event) => setRating(event.target.value as FeedbackRating)}
          >
            {feedbackOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <input
          aria-label="反馈备注"
          value={comment}
          maxLength={1000}
          onChange={(event) => setComment(event.target.value)}
          placeholder="补充说明（可选）"
          className="chat-feedback__comment" />
        <button
          type="button"
          className="chat-feedback__submit"
          disabled={mutation.isPending}
          onClick={() => void submitFeedbackNow()}
        >
          {mutation.isPending ? '提交中…' : '提交反馈'}
        </button>
      </div>
      {statusMessage ? (
        <p role="status" className="chat-feedback__status chat-feedback__status--success">
          {statusMessage}
        </p>
      ) : null}
      {errorMessage ? (
        <p role="alert" className="chat-feedback__status chat-feedback__status--error">
          {errorMessage}
        </p>
      ) : null}
    </div>
  )
}
