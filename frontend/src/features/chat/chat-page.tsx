import {
  AlertOutlined,
  BookOutlined,
  CheckCircleOutlined,
  PlusOutlined,
  SendOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Empty,
  Form,
  Input,
  Select,
  Space,
  Tag,
  Typography,
} from 'antd'
import { useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import type {
  AssistantMetadata,
  ChatResult,
  ConversationMessage,
  FeedbackRating,
} from '../../api/chat'
import type { QueryMode } from '../../api/knowledge-bases'
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
    <div>
      <div className="page-header">
        <div>
          <h2>{title}</h2>
          <p>回答以授权知识库为依据；没有可靠证据时会明确说明。</p>
        </div>
        {conversationId ? (
          <Button icon={<PlusOutlined />} onClick={() => navigate('/chat')}>
            新建问答
          </Button>
        ) : null}
      </div>

      <div
        style={{
          display: 'grid',
          gap: 16,
          gridTemplateColumns: 'minmax(0, 1fr) 300px',
        }}
        className="chat-layout"
      >
        <Card styles={{ body: { padding: 0 } }}>
          <div
            aria-live="polite"
            style={{ minHeight: 460, padding: 20, display: 'grid', gap: 16 }}
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
              <Empty
                image={<ThunderboltOutlined style={{ fontSize: 40, color: '#4f46e5' }} />}
                description={
                  <div>
                    <Typography.Title level={5}>从一个制度问题开始</Typography.Title>
                    <Typography.Text type="secondary">
                      例如：差旅住宿标准是多少？
                    </Typography.Text>
                  </div>
                }
                style={{ margin: '80px 0' }}
              />
            ) : (
              visibleMessages.map((message) => (
                <MessageCard key={message.id} message={message} />
              ))
            )}

            {sendMutation.isPending ? (
              <Card size="small">
                <LoadingState message="正在检索授权知识库并生成回答…" minH="min-h-0" />
              </Card>
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
          </div>

          <div style={{ borderTop: '1px solid #f0f0f0', padding: 16 }}>
            <form
              onSubmit={(event) => {
                event.preventDefault()
                void submit(question)
              }}
            >
              <label
                htmlFor="chat-question"
                style={{ display: 'block', marginBottom: 8, fontWeight: 500 }}
              >
                问题
              </label>
              <Input.TextArea
                id="chat-question"
                rows={3}
                maxLength={4000}
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="输入你的制度问题，尽量描述完整场景"
              />
              <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 12 }}>
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
        </Card>

        <div style={{ display: 'grid', gap: 16, alignContent: 'start' }}>
          <Card title="检索范围" size="small">
            <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
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
                style={{ display: 'grid', gap: 10 }}
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
        </div>
      </div>

      <style>{`
        @media (max-width: 1200px) {
          .chat-layout {
            grid-template-columns: 1fr !important;
          }
        }
      `}</style>
    </div>
  )
}

function MessageCard({ message }: { message: ConversationMessage }) {
  if (message.role === 'user') {
    return (
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <Card
          size="small"
          style={{
            maxWidth: 640,
            background: '#4f46e5',
            color: '#fff',
            border: 'none',
          }}
          styles={{ body: { color: '#fff' } }}
        >
          <Typography.Text style={{ color: 'rgba(255,255,255,0.75)', fontSize: 12 }}>
            你的问题
          </Typography.Text>
          <div style={{ whiteSpace: 'pre-wrap', marginTop: 6 }}>{message.content}</div>
        </Card>
      </div>
    )
  }

  const noEvidence =
    message.metadata.citations.length === 0 && message.metadata.confidenceScore === 0

  return (
    <article aria-label="PolicyFlow 回答">
    <Card
      size="small"
      style={{ maxWidth: 760 }}
      title={
        <Space>
          <ThunderboltOutlined style={{ color: '#4f46e5' }} aria-hidden />
          PolicyFlow 回答
        </Space>
      }
    >
      <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 12 }}>
        {message.content}
      </Typography.Paragraph>

      {noEvidence ? (
        <Alert
          type="warning"
          showIcon
          icon={<AlertOutlined />}
          message="未找到可靠依据"
          description="当前授权知识库没有足够证据，请联系相关部门确认。"
        />
      ) : (
        <CitationList citations={message.metadata.citations} />
      )}

      <Space wrap style={{ marginTop: 12 }}>
        {message.metadata.confidenceScore !== null ? (
          <Tag>
            可信度 {Math.round(message.metadata.confidenceScore * 100)}%
          </Tag>
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
        <Card size="small" type="inner" title="建议能力" style={{ marginTop: 12 }}>
          {message.metadata.suggestedSkills.map((skill) => (
            <div key={skill.name} style={{ fontSize: 12, marginBottom: 4 }}>
              {skill.name}：{skill.description}
            </div>
          ))}
        </Card>
      ) : null}

      {message.metadata.queryLogId ? (
        <FeedbackActions queryLogId={message.metadata.queryLogId} />
      ) : (
        <Typography.Text type="secondary" style={{ display: 'block', marginTop: 12, fontSize: 12 }}>
          当前回答缺少反馈标识，暂不能评价。
        </Typography.Text>
      )}
    </Card>
    </article>
  )
}

function CitationList({ citations }: { citations: AssistantMetadata['citations'] }) {
  if (citations.length === 0) return null
  return (
    <details
      style={{
        marginTop: 4,
        border: '1px solid #f0f0f0',
        borderRadius: 8,
        padding: '8px 12px',
        background: '#fafafa',
      }}
    >
      <summary style={{ cursor: 'pointer', fontWeight: 600 }}>
        查看引用（{citations.length}）
      </summary>
      <Space direction="vertical" style={{ width: '100%', marginTop: 12 }}>
        {citations.map((citation, index) => (
          <Card key={`${citation.documentId ?? citation.knowledgeBaseId}-${index}`} size="small">
            <Space>
              <BookOutlined />
              <strong>
                {citation.knowledgeBaseName} · {citation.documentTitle ?? '未命名文档'}
              </strong>
            </Space>
            <div style={{ marginTop: 6, color: '#5b6577', fontSize: 12 }}>
              {citation.snippet}
            </div>
            {citation.chunkId ? (
              <div style={{ marginTop: 4, color: '#8a93a6', fontSize: 11 }}>
                Chunk：{citation.chunkId}
              </div>
            ) : null}
          </Card>
        ))}
      </Space>
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
    <Form
      layout="inline"
      style={{ marginTop: 16, paddingTop: 12, borderTop: '1px solid #f0f0f0', rowGap: 8 }}
      onFinish={() => mutation.mutate({ rating, comment })}
    >
      <Form.Item label="评价">
        <Select
          aria-label="回答评价"
          style={{ width: 140 }}
          value={rating}
          onChange={(value) => setRating(value as FeedbackRating)}
          options={feedbackOptions}
        />
      </Form.Item>
      <Form.Item style={{ flex: 1, minWidth: 180 }}>
        <Input
          aria-label="反馈备注"
          value={comment}
          maxLength={1000}
          onChange={(event) => setComment(event.target.value)}
          placeholder="补充说明（可选）"
        />
      </Form.Item>
      <Form.Item>
        <Button type="primary" htmlType="submit" loading={mutation.isPending}>
          提交反馈
        </Button>
      </Form.Item>
      {mutation.isSuccess ? (
        <Typography.Text type="success" role="status" style={{ width: '100%', fontSize: 12 }}>
          已记录“{selectedLabel}”，再次提交会覆盖你的上一条反馈。
        </Typography.Text>
      ) : null}
      {mutation.isError ? (
        <Typography.Text type="danger" role="alert" style={{ width: '100%', fontSize: 12 }}>
          {mutation.error.message}
        </Typography.Text>
      ) : null}
    </Form>
  )
}
