import {
  AlertOutlined,
  BookOutlined,
  CheckCircleOutlined,
  CopyOutlined,
  DeleteOutlined,
  EditOutlined,
  HistoryOutlined,
  MessageOutlined,
  PlusOutlined,
  SearchOutlined,
  SendOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
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
  Tag,
  Typography,
} from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import type {
  AssistantMetadata,
  ChatResult,
  ChatStageEvent,
  CommandTrace,
  ConversationMessage,
  ConversationSummary,
  FeedbackRating,
  ToolCallTrace,
  TurnDiagnostics,
  UsedMemoryItem,
} from '../../api/chat'
import type { QueryMode } from '../../api/knowledge-bases'
import { LoadingState } from '../../components/feedback/state-views'
import { MarkdownContent } from '../../components/markdown/markdown-content'
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
}

function resultMessage(result: ChatResult): ConversationMessage {
  return {
    id: result.messageId,
    role: 'assistant',
    content: result.answer,
    createdAt: new Date().toISOString(),
    metadata: {
      citations: result.citations,
      queryLogId: result.queryLogId,
      confidenceScore: result.confidenceScore,
      queryMode: result.queryMode,
      routerResult: result.routerResult,
      suggestedSkills: result.suggestedSkills,
      compliance: result.compliance,
      diagnostics: result.diagnostics,
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

function formatHistoryTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const now = new Date()
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  if (sameDay) {
    return new Intl.DateTimeFormat('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    }).format(date)
  }
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
  }).format(date)
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
  const [thinkingDiagnostics, setThinkingDiagnostics] = useState<TurnDiagnostics>({
    memories: [],
    tools: [],
    commands: [],
  })
  const messagesContainerRef = useRef<HTMLDivElement | null>(null)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    setMessages([])
    setQuestion('')
    setFailedQuestion(null)
    setThinkingStages([])
    setThinkingDiagnostics({ memories: [], tools: [], commands: [] })
  }, [conversationId])

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
  ])

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
    setThinkingStages([])
    setThinkingDiagnostics({ memories: [], tools: [], commands: [] })
    try {
      const result = await sendMutation.mutateAsync({
        conversationId: conversationId || undefined,
        question: normalized,
        knowledgeBaseIds: selectedKnowledgeBases,
        queryMode,
        streamHandlers: {
          onStage: (event) => {
            setThinkingStages((current) => {
              const next = [...current]
              const index = next.findIndex((item) => item.stage === event.stage)
              if (index >= 0) next[index] = event
              else next.push(event)
              return next
            })
          },
          onDiagnosticsPartial: (partial) => {
            setThinkingDiagnostics(partial as TurnDiagnostics)
          },
          onDiagnostics: (diagnostics) => {
            setThinkingDiagnostics(diagnostics)
          },
        },
      })
      setThinkingStages([])
      setThinkingDiagnostics({ memories: [], tools: [], commands: [] })
      setMessages((current) => [...current, resultMessage(result)])
      if (!conversationId) {
        navigate(`/chat/${result.conversationId}`, { replace: true })
      }
    } catch {
      setFailedQuestion(normalized)
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
  const title = conversation.data?.title ?? '新制度问答'
  const historyItems = history.data?.items ?? []

  return (
    <div className="chat-page">
      <div className="page-header chat-page__header">
        <div>
          <h2>{title}</h2>
          <p>回答以授权知识库为依据；历史会话仅展示你自己的记录，并与其他用户隔离。</p>
        </div>
        <Button icon={<PlusOutlined />} onClick={startNewConversation}>
          新建问答
        </Button>
      </div>

      <div className="chat-page__body">
        <aside className="chat-page__history" aria-label="历史会话">
          <Card
            size="small"
            title={
              <Space>
                <HistoryOutlined />
                历史会话
              </Space>
            }
            extra={
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                仅本人可见
              </Typography.Text>
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
                  prefix={<SearchOutlined />}
                  aria-label="搜索历史会话"
                />
              </div>

              <button
                type="button"
                className={`chat-history__item${conversationId ? '' : ' is-active'}`}
                onClick={startNewConversation}
                aria-label="开始新会话"
                aria-current={conversationId ? undefined : 'page'}
              >
                <div className="chat-history__title">
                  <MessageOutlined />
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
                    }
                  />
                </div>
              ) : historyItems.length === 0 ? (
                <div className="chat-history__state">
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description={
                      debouncedHistoryKeyword ? '没有匹配的历史会话' : '还没有历史会话'
                    }
                  />
                </div>
              ) : (
                historyItems.map((item) => (
                  <HistoryItem
                    key={item.id}
                    item={item}
                    active={item.id === conversationId}
                    onOpen={() => openConversation(item.id)}
                    onRename={() => openRename(item)}
                    onDelete={() => confirmDelete(item)}
                  />
                ))
              )}
            </div>
          </Card>
        </aside>

        <Card className="chat-page__main" styles={{ body: { padding: 0, height: '100%' } }}>
          <div className="chat-page__main-inner">
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
                  }
                />
              ) : visibleMessages.length === 0 ? (
                <div className="chat-page__empty">
                  <Empty
                    image={
                      <ThunderboltOutlined
                        className="pf-brand-icon"
                        style={{ fontSize: 40 }}
                      />
                    }
                    description={
                      <div>
                        <Typography.Title level={5} style={{ marginBottom: 4 }}>
                          从一个制度问题开始
                        </Typography.Title>
                        <Typography.Text type="secondary">
                          例如：差旅住宿标准是多少？
                        </Typography.Text>
                      </div>
                    }
                  />
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
                  />
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
                        <ThunderboltOutlined
                          style={{ color: palette.primary }}
                          aria-hidden
                          className="chat-thinking__pulse"
                        />
                        <span>思考中</span>
                        <Tag color="processing">流式执行</Tag>
                      </Space>
                    </div>
                    <ThinkingStreamPanel
                      stages={thinkingStages}
                      diagnostics={thinkingDiagnostics}
                    />
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
                  }
                />
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
                <label htmlFor="chat-question" className="chat-page__composer-label">
                  问题
                </label>
                <Input.TextArea
                  id="chat-question"
                  rows={3}
                  maxLength={4000}
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  placeholder="输入你的制度问题，尽量描述完整场景"
                  onPressEnter={(event) => {
                    if (!event.shiftKey) {
                      event.preventDefault()
                      void submit(question)
                    }
                  }}
                />
                <div className="chat-page__composer-actions">
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    Enter 发送 · Shift + Enter 换行
                  </Typography.Text>
                  <Button
                    type="primary"
                    htmlType="submit"
                    loading={sendMutation.isPending}
                    disabled={!question.trim() || noKnowledgeBases}
                  >
                    <SendOutlined aria-hidden />
                    发送问题
                  </Button>
                </div>
              </form>
            </div>
          </div>
        </Card>

        <aside className="chat-page__sidebar">
          <Card title="检索范围" size="small">
            <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 12 }}>
              不选择时检索全部可访问知识库。
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
                }
              />
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
                    <span>
                      <div style={{ fontWeight: 500 }}>{kb.name}</div>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {kb.code}
                      </Typography.Text>
                    </span>
                  ),
                  value: kb.id,
                }))}
              />
            )}
          </Card>

          <Card title="检索模式" size="small">
            <Select
              style={{ width: '100%' }}
              value={queryMode}
              onChange={(value) => setQueryMode(value as QueryMode)}
              options={queryModeOptions}
            />
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
          onPressEnter={() => void confirmRename()}
        />
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
          <EditOutlined />
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
          <DeleteOutlined />
        </button>
      </div>
    </div>
  )
}

function MessageCard({
  message,
  onCopy,
  onEditQuestion,
}: {
  message: ConversationMessage
  onCopy: () => void
  onEditQuestion?: () => void
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
              icon={<CopyOutlined />}
              aria-label="复制问题"
              onClick={onCopy}
            >
              复制
            </Button>
            {onEditQuestion ? (
              <Button
                type="text"
                size="small"
                icon={<EditOutlined />}
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

  const noEvidence =
    message.metadata.citations.length === 0 &&
    (message.metadata.confidenceScore === 0 ||
      message.metadata.compliance?.warnings?.includes('NO_RELIABLE_EVIDENCE') === true)

  return (
    <div className="chat-row chat-row--assistant">
      <article
        aria-label="PolicyFlow 回答"
        className={`chat-bubble chat-bubble--assistant${noEvidence ? ' chat-bubble--warning' : ''}`}
      >
        <div className="chat-bubble__title">
          <Space size={8} wrap>
            <ThunderboltOutlined
              style={{ color: noEvidence ? palette.warning : palette.primary }}
              aria-hidden
            />
            <span>PolicyFlow 回答</span>
            {noEvidence ? <Tag color="warning">模型参考 · 不可信</Tag> : null}
          </Space>
        </div>

        {noEvidence ? (
          <Alert
            type="warning"
            showIcon
            icon={<AlertOutlined />}
            style={{ marginBottom: 12 }}
            message="未找到可靠依据"
            description="当前授权知识库没有检索到制度证据。下面是模型基于通用知识给出的参考回答，不能作为正式制度依据，请你自行判断，并与相关部门确认后再执行。"
          />
        ) : null}

        <MarkdownContent
          className="chat-bubble__content chat-bubble__content--assistant"
          content={message.content}
        />

        {noEvidence ? null : <CitationList citations={message.metadata.citations} />}

        <TurnDiagnosticsPanel diagnostics={message.metadata.diagnostics} />

        <Space wrap style={{ marginTop: 12 }}>
          {noEvidence ? (
            <Tag color="orange">可信度 0% · 需人工判断</Tag>
          ) : message.metadata.confidenceScore !== null ? (
            <Tag>可信度 {Math.round(message.metadata.confidenceScore * 100)}%</Tag>
          ) : null}
          {message.metadata.queryMode ? (
            <Tag color="blue">
              {queryModeLabels[message.metadata.queryMode] ?? message.metadata.queryMode}
            </Tag>
          ) : null}
          {message.metadata.compliance?.passed ? (
            <Tag icon={<CheckCircleOutlined />} color="success">
              合规通过
            </Tag>
          ) : null}
        </Space>

        {message.metadata.suggestedSkills.length > 0 ? (
          <div className="chat-bubble__skills">
            <div className="chat-bubble__skills-title">建议能力</div>
            {message.metadata.suggestedSkills.map((skill) => (
              <div key={skill.name} style={{ fontSize: 12, marginBottom: 4 }}>
                {skill.name}：{skill.description}
              </div>
            ))}
          </div>
        ) : null}

        {message.metadata.queryLogId ? (
          <FeedbackActions queryLogId={message.metadata.queryLogId} />
        ) : (
          <Typography.Text
            type="secondary"
            style={{ display: 'block', marginTop: 12, fontSize: 12 }}
          >
            当前回答缺少反馈标识，暂不能评价。
          </Typography.Text>
        )}

        <div className="chat-bubble__actions chat-bubble__actions--footer">
          <Button
            type="text"
            size="small"
            icon={<CopyOutlined />}
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
    <details className="chat-citations" open>
      <summary>查看引用（{citations.length}）</summary>
      <Space direction="vertical" style={{ width: '100%', marginTop: 12 }}>
        {citations.map((citation, index) => (
          <div
            key={`${citation.documentId ?? citation.knowledgeBaseId}-${index}`}
            className="chat-citations__item"
          >
            <Space>
              <BookOutlined className="pf-brand-icon" />
              <strong>
                {citation.knowledgeBaseName} · {citation.documentTitle ?? '未命名文档'}
              </strong>
            </Space>
            <p className="chat-citations__snippet">{citation.snippet}</p>
            {citation.chunkId ? (
              <p className="chat-citations__chunk">Chunk：{citation.chunkId}</p>
            ) : null}
          </div>
        ))}
      </Space>
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

function ThinkingStreamPanel({
  stages,
  diagnostics,
}: {
  stages: ChatStageEvent[]
  diagnostics: TurnDiagnostics
}) {
  const memoryCount = diagnostics.memories.length
  const toolCount = diagnostics.tools.length
  const commandCount = Math.max(diagnostics.commands.length, stages.length)

  return (
    <div className="chat-thinking" aria-live="polite">
      <div className="chat-diagnostics__overview">
        <span className="chat-diagnostics__overview-label">本轮使用</span>
        <Tag color="purple">记忆 {memoryCount}</Tag>
        <Tag color="geekblue">工具 {toolCount}</Tag>
        <Tag color="cyan">命令 {commandCount}</Tag>
      </div>

      <div className="chat-thinking__stages">
        {(stages.length > 0
          ? stages
          : [{ stage: '准备中', status: 'running', message: '正在初始化本轮执行…' }]
        ).map((stage) => (
          <div key={stage.stage} className="chat-thinking__stage">
            <div className="chat-thinking__stage-head">
              <strong>{stage.stage}</strong>
              <Tag
                color={
                  stage.status === 'running'
                    ? 'processing'
                    : stage.status === 'success'
                      ? 'success'
                      : stage.status === 'warning'
                        ? 'warning'
                        : stage.status === 'empty' || stage.status === 'skipped'
                          ? 'default'
                          : 'error'
                }
              >
                {stage.status}
              </Tag>
            </div>
            <div className="chat-thinking__stage-message">{stage.message}</div>
          </div>
        ))}
      </div>

      {memoryCount + toolCount + diagnostics.commands.length > 0 ? (
        <TurnDiagnosticsPanel diagnostics={diagnostics} compact />
      ) : (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          记忆 / 工具 / 命令将在执行过程中逐步出现，可点击展开查看详情。
        </Typography.Text>
      )}
    </div>
  )
}

function TurnDiagnosticsPanel({
  diagnostics,
  compact = false,
}: {
  diagnostics?: TurnDiagnostics | null
  compact?: boolean
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
          <Tag color="purple">记忆 {memoryCount}</Tag>
          <Tag color="geekblue">工具 {toolCount}</Tag>
          <Tag color="cyan">命令 {commandCount}</Tag>
        </div>
      )}

      {memoryCount > 0 ? (
        <details className="chat-diagnostics__section">
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
        <details className="chat-diagnostics__section">
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
        <details className="chat-diagnostics__section">
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
        <Tag color="purple">{memoryTypeLabel[item.memoryType] ?? item.memoryType}</Tag>
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
        <Tag color={item.status === 'success' || item.status === 'suggested' ? 'blue' : 'error'}>
          {item.status}
        </Tag>
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
        <Tag
          color={
            item.status === 'success'
              ? 'success'
              : item.status === 'warning'
                ? 'warning'
                : item.status === 'skipped' || item.status === 'empty'
                  ? 'default'
                  : 'error'
          }
        >
          {item.status}
        </Tag>
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
          className="chat-feedback__comment"
        />
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
