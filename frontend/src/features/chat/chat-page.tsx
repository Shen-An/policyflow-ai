import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  MessageSquareText,
  Send,
  Sparkles,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import type {
  AssistantMetadata,
  ChatResult,
  ConversationMessage,
  FeedbackRating,
} from '../../api/chat'
import type { QueryMode } from '../../api/knowledge-bases'
import { Button } from '../../components/ui/button'
import { Alert } from '../../components/feedback/alert'
import { LoadingState } from '../../components/feedback/state-views'
import { useKnowledgeBasesQuery } from '../knowledge-bases/queries'
import {
  useConversationQuery,
  useFeedbackMutation,
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
    },
  }
}

const queryModeLabels: Record<string, { label: string; hint: string }> = {
  hybrid: { label: '混合模式', hint: '根据问题智能选择' },
  mix: { label: '全局+局部混合', hint: '同时搜索索引与全文' },
  local: { label: '局部搜索', hint: '在相关索引片段中搜索' },
  global: { label: '全局搜索', hint: '在所有文档中全文搜索' },
  naive: { label: '朴素搜索', hint: '直接检索，不做语义优化' },
}

export function ChatPage() {
  const { conversationId = '' } = useParams()
  const navigate = useNavigate()
  const conversation = useConversationQuery(conversationId)
  const knowledgeBases = useKnowledgeBasesQuery()
  const sendMutation = useSendChatMutation()
  const [messages, setMessages] = useState<ConversationMessage[]>([])
  const [question, setQuestion] = useState('')
  const [selectedKnowledgeBases, setSelectedKnowledgeBases] = useState<string[]>([])
  const [queryMode, setQueryMode] = useState<QueryMode>('hybrid')
  const [failedQuestion, setFailedQuestion] = useState<string | null>(null)

  const visibleMessages =
    messages.length > 0 ? messages : conversation.data?.messages ?? []

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
    try {
      const result = await sendMutation.mutateAsync({
        conversationId: conversationId || undefined,
        question: normalized,
        knowledgeBaseIds: selectedKnowledgeBases,
        queryMode,
      })
      setMessages((current) => [...current, resultMessage(result)])
      if (!conversationId) {
        navigate(`/chat/${result.conversationId}`, { replace: true })
      }
    } catch {
      setFailedQuestion(normalized)
    }
  }

  const noKnowledgeBases = knowledgeBases.isSuccess && knowledgeBases.data.length === 0
  const title = conversation.data?.title ?? '新制度问答'

  return (
    <section className="mx-auto max-w-6xl">
      <div className="flex flex-col gap-[var(--space-3)] sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-2xl font-semibold">{title}</h2>
          <p className="mt-[var(--space-1)] text-sm text-[var(--color-text-secondary)]">
            回答以授权知识库为依据；没有可靠证据时会明确说明。
          </p>
        </div>
        {conversationId ? (
          <Button
            variant="secondary"
            onClick={() => navigate('/chat')}
          >
            新建问答
          </Button>
        ) : null}
      </div>

      <div className="mt-[var(--space-6)] grid gap-[var(--space-6)] xl:grid-cols-[minmax(0,1fr)_280px]">
        <div className="rounded-xl border border-[var(--color-border)] bg-white shadow-sm">
          <div
            aria-live="polite"
            className="min-h-[420px] space-y-[var(--space-4)] p-[var(--space-4)] sm:p-[var(--space-6)]"
          >
            {conversation.isPending && conversationId && visibleMessages.length === 0 ? (
              <LoadingState message="正在加载会话…" minH="min-h-0" />
            ) : conversation.isError ? (
              <Alert tone="danger" title="会话加载失败" action={<Button onClick={() => void conversation.refetch()}>重新加载</Button>}>
                <p>{conversation.error.message}</p>
              </Alert>
            ) : visibleMessages.length === 0 ? (
              <div className="grid min-h-[360px] place-items-center text-center">
                <div>
                  <MessageSquareText
                    aria-hidden="true"
                    className="mx-auto size-9 text-[var(--color-primary)]"
                  />
                  <h3 className="mt-[var(--space-3)] font-semibold">从一个制度问题开始</h3>
                  <p className="mt-[var(--space-1)] text-sm text-[var(--color-text-secondary)]">
                    例如：差旅住宿标准是多少？
                  </p>
                </div>
              </div>
            ) : (
              visibleMessages.map((message) => (
                <MessageCard key={message.id} message={message} />
              ))
            )}

            {sendMutation.isPending ? (
              <div className="rounded-lg bg-slate-50 p-[var(--space-4)]">
                <LoadingState message="正在检索授权知识库并生成回答…" minH="min-h-0" />
              </div>
            ) : null}
            {sendMutation.isError && failedQuestion ? (
              <Alert tone="danger" action={<Button onClick={() => void submit(failedQuestion, false)}>重试发送</Button>}>
                <p>{sendMutation.error.message}</p>
              </Alert>
            ) : null}
          </div>

          <form
            className="border-t border-[var(--color-border)] p-[var(--space-4)]"
            onSubmit={(event) => {
              event.preventDefault()
              void submit(question)
            }}
          >
            <label className="text-sm font-semibold" htmlFor="chat-question">
              问题
            </label>
            <textarea
              id="chat-question"
              rows={3}
              maxLength={4000}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="输入你的制度问题"
              className="mt-[var(--space-2)] w-full resize-y rounded-md border border-[var(--color-border)] p-[var(--space-3)]"
            />
            <div className="mt-[var(--space-3)] flex items-center justify-between gap-[var(--space-3)]">
              <span className="text-xs text-[var(--color-text-secondary)]">
                {question.length} / 4000
              </span>
              <Button
                type="submit"
                disabled={!question.trim() || sendMutation.isPending || noKnowledgeBases}
              >
                <Send aria-hidden="true" className="size-4" />
                发送问题
              </Button>
            </div>
          </form>
        </div>

        <aside className="space-y-[var(--space-4)]">
          <div className="rounded-xl border border-[var(--color-border)] bg-white p-[var(--space-4)] shadow-sm">
            <h3 className="font-semibold">检索范围</h3>
            <p className="mt-[var(--space-1)] text-xs text-[var(--color-text-secondary)]">
              不选择时检索全部可访问知识库。
            </p>
            {knowledgeBases.isPending ? (
              <p role="status" className="mt-[var(--space-3)] text-sm">正在加载知识库…</p>
            ) : knowledgeBases.isError ? (
              <Alert className="mt-[var(--space-3)]" tone="danger" action={<Button variant="ghost" className="min-h-11" onClick={() => void knowledgeBases.refetch()}>重试</Button>}>
                知识库加载失败
              </Alert>
            ) : noKnowledgeBases ? (
              <p className="mt-[var(--space-3)] text-sm">
                还没有可访问的知识库，请联系知识库管理员授权。
              </p>
            ) : (
              <div className="mt-[var(--space-3)] space-y-[var(--space-2)]">
                {knowledgeBases.data.map((knowledgeBase) => (
                  <label key={knowledgeBase.id} className="flex items-start gap-[var(--space-2)] text-sm">
                    <input
                      type="checkbox"
                      checked={selectedKnowledgeBases.includes(knowledgeBase.id)}
                      onChange={(event) => {
                        setSelectedKnowledgeBases((current) =>
                          event.target.checked
                            ? [...current, knowledgeBase.id]
                            : current.filter((id) => id !== knowledgeBase.id),
                        )
                      }}
                    />
                    <span>{knowledgeBase.name}</span>
                  </label>
                ))}
              </div>
            )}
          </div>

          <label className="block rounded-xl border border-[var(--color-border)] bg-white p-[var(--space-4)] text-sm font-semibold shadow-sm">
            检索模式
            <select
              value={queryMode}
              onChange={(event) => setQueryMode(event.target.value as QueryMode)}
              className="mt-[var(--space-2)] min-h-11 w-full rounded-md border border-[var(--color-border)] px-[var(--space-3)] font-normal"
            >
              {['hybrid', 'mix', 'local', 'global', 'naive'].map((mode) => (
                <option key={mode} value={mode}>{queryModeLabels[mode].label} — {queryModeLabels[mode].hint}</option>
              ))}
            </select>
          </label>
        </aside>
      </div>
    </section>
  )
}

function MessageCard({ message }: { message: ConversationMessage }) {
  if (message.role === 'user') {
    return (
      <article className="ml-auto max-w-2xl rounded-xl bg-[var(--color-primary-50)] p-[var(--space-4)]">
        <p className="text-xs font-semibold text-[var(--color-primary-700)]">你的问题</p>
        <p className="mt-[var(--space-2)] whitespace-pre-wrap text-sm">{message.content}</p>
      </article>
    )
  }

  const noEvidence =
    message.metadata.citations.length === 0 &&
    message.metadata.confidenceScore === 0

  return (
    <article
      aria-label="PolicyFlow 回答"
      className="max-w-3xl rounded-xl border border-[var(--color-border)] p-[var(--space-4)]"
    >
      <div className="flex items-center gap-[var(--space-2)]">
        <Sparkles aria-hidden="true" className="size-4 text-[var(--color-primary)]" />
        <h3 className="text-sm font-semibold">PolicyFlow 回答</h3>
      </div>
      <p className="mt-[var(--space-3)] whitespace-pre-wrap text-sm leading-6">
        {message.content}
      </p>

      {noEvidence ? (
        <Alert tone="warning" title={
          <span className="flex items-center gap-[var(--space-2)]">
            <AlertTriangle aria-hidden="true" className="size-4" />
            未找到可靠依据
          </span>
        }>
          当前授权知识库没有足够证据，请联系相关部门确认。
        </Alert>
      ) : (
        <CitationList citations={message.metadata.citations} />
      )}

      <div className="mt-[var(--space-4)] flex flex-wrap gap-[var(--space-2)] text-xs text-[var(--color-text-secondary)]">
        {message.metadata.confidenceScore !== null ? (
          <span>可信度 {Math.round(message.metadata.confidenceScore * 100)}%</span>
        ) : null}
        {message.metadata.queryMode ? <span>· {queryModeLabels[message.metadata.queryMode]?.label ?? message.metadata.queryMode}</span> : null}
        {message.metadata.compliance?.passed ? (
          <span className="inline-flex items-center gap-1 text-[var(--color-success-700)]">
            <CheckCircle2 aria-hidden="true" className="size-3" />合规检查通过
          </span>
        ) : null}
      </div>

      {message.metadata.suggestedSkills.length > 0 ? (
        <div className="mt-[var(--space-4)] rounded-lg bg-slate-50 p-[var(--space-3)]">
          <p className="text-xs font-semibold">建议能力</p>
          {message.metadata.suggestedSkills.map((skill) => (
            <p key={skill.name} className="mt-[var(--space-1)] text-xs">
              {skill.name}：{skill.description}
            </p>
          ))}
        </div>
      ) : null}

      {message.metadata.queryLogId ? (
        <FeedbackActions queryLogId={message.metadata.queryLogId} />
      ) : (
        <p className="mt-[var(--space-4)] text-xs text-[var(--color-text-secondary)]">
          当前回答缺少反馈标识，暂不能评价。
        </p>
      )}
    </article>
  )
}

function CitationList({ citations }: { citations: AssistantMetadata['citations'] }) {
  if (citations.length === 0) return null
  return (
    <details className="mt-[var(--space-4)] rounded-lg border border-[var(--color-border)]">
      <summary className="cursor-pointer px-[var(--space-3)] py-[var(--space-2)] text-sm font-semibold">
        查看引用（{citations.length}）
      </summary>
      <div className="space-y-[var(--space-3)] border-t border-[var(--color-border)] p-[var(--space-3)]">
        {citations.map((citation, index) => (
          <div key={`${citation.documentId ?? citation.knowledgeBaseId}-${index}`}>
            <p className="flex items-center gap-[var(--space-2)] text-xs font-semibold">
              <BookOpen aria-hidden="true" className="size-3" />
              {citation.knowledgeBaseName} · {citation.documentTitle ?? '未命名文档'}
            </p>
            <p className="mt-[var(--space-1)] text-xs leading-5 text-[var(--color-text-secondary)]">
              {citation.snippet}
            </p>
            {citation.chunkId ? (
              <p className="mt-[var(--space-1)] text-[11px] text-[var(--color-text-secondary)]">
                Chunk：{citation.chunkId}
              </p>
            ) : null}
          </div>
        ))}
      </div>
    </details>
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
  const selectedLabel = useMemo(
    () => feedbackOptions.find((option) => option.value === rating)?.label,
    [rating],
  )

  return (
    <form
      className="mt-[var(--space-4)] border-t border-[var(--color-border)] pt-[var(--space-3)]"
      onSubmit={(event) => {
        event.preventDefault()
        mutation.mutate({ rating, comment })
      }}
    >
      <div className="flex flex-col gap-[var(--space-2)] sm:flex-row">
        <label className="text-xs font-semibold">
          评价
          <select
            aria-label="回答评价"
            value={rating}
            onChange={(event) => setRating(event.target.value as FeedbackRating)}
            className="ml-[var(--space-2)] min-h-11 rounded-md border border-[var(--color-border)] px-[var(--space-2)] font-normal"
          >
            {feedbackOptions.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
        <input
          aria-label="反馈备注"
          value={comment}
          maxLength={1000}
          onChange={(event) => setComment(event.target.value)}
          placeholder="补充说明（可选）"
          className="min-h-11 flex-1 rounded-md border border-[var(--color-border)] px-[var(--space-2)] text-xs"
        />
        <Button type="submit" className="min-h-11 py-1 text-xs" disabled={mutation.isPending}>
          {mutation.isPending ? '提交中…' : '提交反馈'}
        </Button>
      </div>
      {mutation.isSuccess ? (
        <p role="status" className="mt-[var(--space-2)] text-xs text-[var(--color-success-700)]">
          已记录“{selectedLabel}”，再次提交会覆盖你的上一条反馈。
        </p>
      ) : null}
      {mutation.isError ? (
        <p role="alert" className="mt-[var(--space-2)] text-xs text-[var(--color-danger)]">
          {mutation.error.message}
        </p>
      ) : null}
    </form>
  )
}
