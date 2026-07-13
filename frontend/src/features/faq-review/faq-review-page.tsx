import { FileSearchOutlined } from '@ant-design/icons'
import {
  Alert as AntdAlert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Form,
  Input,
  Modal,
  Row,
  Select,
  Space,
  Tag,
  Typography,
} from 'antd'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { FAQDraft } from '../../api/faq'
import { Alert } from '../../components/feedback/alert'
import { LoadingState } from '../../components/feedback/state-views'
import { useDocumentStatusQuery } from '../documents/queries'
import { useKnowledgeBasesQuery } from '../knowledge-bases/queries'
import {
  useApproveFAQMutation,
  useFAQDraftsQuery,
  useRejectFAQMutation,
} from './queries'

const statusLabel: Record<string, { text: string; color: string }> = {
  draft: { text: '待审核', color: 'processing' },
  pending_review: { text: '待审核', color: 'processing' },
  approved: { text: '已通过', color: 'success' },
  rejected: { text: '已驳回', color: 'error' },
}

export function FAQReviewPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [rejecting, setRejecting] = useState<FAQDraft | null>(null)
  const [approvedDocumentId, setApprovedDocumentId] = useState<string | null>(null)
  const knowledgeBaseId = searchParams.get('knowledge_base_id') ?? ''
  const status = searchParams.get('status') ?? 'draft'
  const knowledgeBases = useKnowledgeBasesQuery()
  const query = useFAQDraftsQuery(knowledgeBaseId, status)
  const approve = useApproveFAQMutation()

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    setSearchParams(next, { replace: true })
  }

  async function approveItem(item: FAQDraft) {
    // Keep window.confirm so existing tests that spy on it continue to pass.
    if (!window.confirm('审核通过后会创建知识文档并触发增量索引，是否继续？')) return
    const result = await approve.mutateAsync(item.id)
    setApprovedDocumentId(result.documentId)
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h2>FAQ 审核</h2>
          <p>审核通过会写入知识库并触发索引；驳回必须填写原因。</p>
        </div>
      </div>

      <Card styles={{ body: { paddingBottom: 8 } }} style={{ marginBottom: 16 }}>
        <div className="page-toolbar">
          <Space wrap>
            <Select
              allowClear
              placeholder="全部知识库"
              style={{ width: 180 }}
              value={knowledgeBaseId || undefined}
              onChange={(value) => setFilter('knowledge_base_id', value ?? '')}
              options={(knowledgeBases.data ?? []).map((kb) => ({
                value: kb.id,
                label: kb.name,
              }))}
            />
            <Select
              allowClear
              placeholder="全部状态"
              style={{ width: 140 }}
              value={status || undefined}
              onChange={(value) => setFilter('status', value ?? '')}
              options={[
                { value: 'draft', label: '待审核' },
                { value: 'approved', label: '已通过' },
                { value: 'rejected', label: '已驳回' },
              ]}
            />
          </Space>
        </div>
      </Card>

      {approvedDocumentId ? <IndexStatus documentId={approvedDocumentId} /> : null}
      {approve.isError ? (
        <AntdAlert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          title={approve.error.message}
        />
      ) : null}

      {query.isPending ? (
        <Card>
          <LoadingState message="正在加载 FAQ…" minH="min-h-48" />
        </Card>
      ) : query.isError ? (
        <AntdAlert
          type="error"
          showIcon
          message="FAQ 加载失败"
          description={query.error.message}
          action={
            <Button size="small" onClick={() => void query.refetch()}>
              重新加载
            </Button>
          }
        />
      ) : query.data.length === 0 ? (
        <Card>
          <Empty
            image={<FileSearchOutlined style={{ fontSize: 40, color: '#94a3b8' }} />}
            description="没有符合条件的 FAQ"
          />
        </Card>
      ) : (
        <Row gutter={[16, 16]}>
          {query.data.map((item) => {
            const meta = statusLabel[item.status] ?? {
              text: item.status,
              color: 'default',
            }
            return (
              <Col key={item.id} xs={24} lg={12}>
                <article>
                  <Card
                    title={item.question}
                    extra={<Tag color={meta.color}>{meta.text}</Tag>}
                  >
                    <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 16 }}>
                      {item.answer}
                    </Typography.Paragraph>
                    <Descriptions size="small" column={1} bordered>
                      <Descriptions.Item label="知识库">
                        {item.knowledgeBaseName}
                      </Descriptions.Item>
                      <Descriptions.Item label="来源文档">
                        {item.sourceDocumentTitle ?? '无'}
                      </Descriptions.Item>
                      {item.reviewNote ? (
                        <Descriptions.Item label="审核备注">
                          {item.reviewNote}
                        </Descriptions.Item>
                      ) : null}
                    </Descriptions>
                    {item.status === 'draft' || item.status === 'pending_review' ? (
                      <Space style={{ marginTop: 16, width: '100%', justifyContent: 'flex-end' }}>
                        <Button autoInsertSpace={false} onClick={() => setRejecting(item)}>
                          驳回
                        </Button>
                        <Button
                          type="primary"
                          autoInsertSpace={false}
                          loading={approve.isPending}
                          onClick={() => void approveItem(item)}
                        >
                          审核通过
                        </Button>
                      </Space>
                    ) : null}
                  </Card>
                </article>
              </Col>
            )
          })}
        </Row>
      )}

      <RejectDialog
        item={rejecting}
        onOpenChange={(open) => {
          if (!open) setRejecting(null)
        }}
      />
    </div>
  )
}

function IndexStatus({ documentId }: { documentId: string }) {
  const query = useDocumentStatusQuery(documentId, 'pending')
  const status = query.data?.indexStatus ?? 'pending'
  // Keep custom Alert so role="status" is preserved for tests.
  return <Alert tone="info" className="mt-4">FAQ 文档索引状态：{status}</Alert>
}

function RejectDialog({
  item,
  onOpenChange,
}: {
  item: FAQDraft | null
  onOpenChange: (open: boolean) => void
}) {
  const mutation = useRejectFAQMutation()
  const [reason, setReason] = useState('')

  async function submit() {
    if (!item || !reason.trim()) return
    await mutation.mutateAsync({ id: item.id, reason: reason.trim() })
    setReason('')
    onOpenChange(false)
  }

  return (
    <Modal
      title="驳回 FAQ"
      open={Boolean(item)}
      onCancel={() => {
        if (!mutation.isPending) {
          setReason('')
          onOpenChange(false)
        }
      }}
      footer={
        <Space>
          <Button
            autoInsertSpace={false}
            onClick={() => {
              setReason('')
              onOpenChange(false)
            }}
          >
            取消
          </Button>
          <Button
            type="primary"
            danger
            autoInsertSpace={false}
            disabled={!reason.trim() || mutation.isPending}
            loading={mutation.isPending}
            onClick={() => {
              void submit()
            }}
          >
            确认驳回
          </Button>
        </Space>
      }
      destroyOnHidden
      afterOpenChange={(open) => {
        if (!open) setReason('')
      }}
    >
      <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
        请说明驳回原因，最多 1000 字。
      </Typography.Paragraph>
      {mutation.isError ? (
        <AntdAlert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          title={mutation.error.message}
        />
      ) : null}
      <Form layout="vertical" requiredMark={false}>
        <Form.Item label="驳回原因" required>
          <Input.TextArea
            rows={5}
            maxLength={1000}
            showCount
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            aria-label="驳回原因"
          />
        </Form.Item>
      </Form>
    </Modal>
  )
}
