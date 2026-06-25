import {
  ArrowLeftOutlined,
  DeleteOutlined,
  DownloadOutlined,
  SaveOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Collapse,
  Form,
  Input,
  Modal,
  Space,
  Tag,
  Typography,
} from 'antd'
import { useState } from 'react'
import {
  Link,
  useBeforeUnload,
  useBlocker,
  useNavigate,
  useParams,
} from 'react-router-dom'
import { ErrorState, LoadingState } from '../../components/feedback/state-views'
import { downloadMarkdown } from './download'
import { confirmAction } from '../../lib/confirm'
import {
  useConfirmDraftMutation,
  useDiscardDraftMutation,
  useDraftQuery,
  useExportDraftMutation,
  useUpdateDraftMutation,
} from './queries'

const statusMap: Record<string, { label: string; color: string }> = {
  draft: { label: '草稿', color: 'default' },
  confirmed: { label: '已确认', color: 'success' },
  discarded: { label: '已丢弃', color: 'error' },
  exported: { label: '已导出', color: 'processing' },
}

export function DraftDetailPage() {
  const { draftId = '' } = useParams()
  return <DraftDetailScreen key={draftId} draftId={draftId} />
}

function DraftDetailScreen({ draftId }: { draftId: string }) {
  const navigate = useNavigate()
  const query = useDraftQuery(draftId)
  const update = useUpdateDraftMutation(draftId)
  const confirm = useConfirmDraftMutation(draftId)
  const discard = useDiscardDraftMutation(draftId)
  const exportMutation = useExportDraftMutation(draftId)
  const [titleOverride, setTitleOverride] = useState<string | null>(null)
  const [contentOverride, setContentOverride] = useState<string | null>(null)
  const title = titleOverride ?? query.data?.title ?? ''
  const content = contentOverride ?? query.data?.content ?? ''

  const dirty = Boolean(
    query.data && (title !== query.data.title || content !== query.data.content),
  )
  const editable = query.data?.status === 'draft'
  const blocker = useBlocker(dirty)
  const leaveOpen = blocker.state === 'blocked'

  useBeforeUnload((event) => {
    if (!dirty) return
    event.preventDefault()
  })

  if (query.isPending) return <LoadingState message="正在加载草稿…" />
  if (query.isError) {
    return (
      <ErrorState
        error={query.error}
        onRetry={() => void query.refetch()}
        title="草稿加载失败"
      />
    )
  }

  const draft = query.data
  const statusMeta = statusMap[draft.status] ?? {
    label: draft.status,
    color: 'default',
  }

  async function save() {
    await update.mutateAsync({ title: title.trim(), content: content.trim() })
  }

  async function confirmDraft() {
    if (dirty) return
    await confirm.mutateAsync()
  }

  function discardDraft() {
    confirmAction({
      title: '丢弃这份草稿？',
      content: '确定丢弃这份草稿吗？此操作会改变草稿状态。',
      okText: '丢弃',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => discard.mutateAsync(),
    })
  }

  async function exportDraft() {
    const result = await exportMutation.mutateAsync()
    downloadMarkdown(title, result.content)
  }

  const actionError =
    update.error ?? confirm.error ?? discard.error ?? exportMutation.error

  return (
    <div style={{ maxWidth: 960, margin: '0 auto' }}>
      <Modal
        title="离开当前草稿？"
        open={leaveOpen}
        okText="离开"
        cancelText="继续编辑"
        okButtonProps={{ danger: true, autoInsertSpace: false }}
        cancelButtonProps={{ autoInsertSpace: false }}
        onOk={() => blocker.proceed?.()}
        onCancel={() => blocker.reset?.()}
        destroyOnHidden
      >
        草稿有未保存修改，确定离开吗？
      </Modal>
      <Space style={{ marginBottom: 16 }}>
        <Button>
          <Link to="/drafts"><ArrowLeftOutlined aria-hidden /> 返回草稿</Link>
        </Button>
        {dirty ? <Tag color="warning">有未保存修改</Tag> : null}
      </Space>

      <Card
        title={
          <Space orientation="vertical" size={2}>
            <Typography.Text type="secondary">{draft.draftType}</Typography.Text>
            <Typography.Title level={4} style={{ margin: 0 }}>
              {draft.title}
            </Typography.Title>
          </Space>
        }
        extra={<Tag color={statusMeta.color}>{statusMeta.label}</Tag>}
      >
        {!editable ? (
          <Alert
            type="info"
            showIcon
            icon={<SafetyCertificateOutlined />}
            style={{ marginBottom: 16 }}
            message={`当前状态为 ${draft.status}，正文已只读。`}
          />
        ) : null}

        {actionError ? (
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 16 }}
            message={actionError.message}
          />
        ) : null}

        <Form layout="vertical" requiredMark={false}>
          <Form.Item label="标题">
            <Input
              value={title}
              maxLength={255}
              disabled={!editable}
              onChange={(event) => setTitleOverride(event.target.value)}
              aria-label="标题"
            />
          </Form.Item>
          <Form.Item label="正文">
            <Input.TextArea
              value={content}
              rows={16}
              disabled={!editable}
              onChange={(event) => setContentOverride(event.target.value)}
              aria-label="正文"
              style={{ lineHeight: 1.7 }}
            />
          </Form.Item>
        </Form>

        <Card size="small" type="inner" title="来源问题" style={{ marginBottom: 16 }}>
          <Typography.Paragraph style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}>
            {draft.sourceQuestion || '无'}
          </Typography.Paragraph>
        </Card>

        {draft.relatedSources.length > 0 ? (
          <Collapse
            style={{ marginBottom: 16 }}
            items={[
              {
                key: 'sources',
                label: `查看关联来源（${draft.relatedSources.length}）`,
                children: (
                  <pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>
                    {JSON.stringify(draft.relatedSources, null, 2)}
                  </pre>
                ),
              },
            ]}
          />
        ) : null}

        <Space wrap style={{ width: '100%', justifyContent: 'flex-end' }}>
          {editable ? (
            <>
              <Button
                disabled={!dirty || update.isPending || !title.trim() || !content.trim()}
                loading={update.isPending}
                onClick={() => void save()}
              >
                <SaveOutlined aria-hidden />
                保存草稿
              </Button>
              <Button
                type="primary"
                disabled={dirty || confirm.isPending}
                loading={confirm.isPending}
                onClick={() => void confirmDraft()}
              >
                <SafetyCertificateOutlined aria-hidden />
                确认草稿
              </Button>
              <Button
                danger
                loading={discard.isPending}
                onClick={() => void discardDraft()}
              >
                <DeleteOutlined aria-hidden />
                丢弃草稿
              </Button>
            </>
          ) : null}
          {draft.status !== 'discarded' ? (
            <Button
              disabled={dirty || exportMutation.isPending}
              loading={exportMutation.isPending}
              onClick={() => void exportDraft()}
            >
              <DownloadOutlined aria-hidden />
              导出 Markdown
            </Button>
          ) : null}
        </Space>
      </Card>

      <button type="button" className="sr-only" onClick={() => navigate('/drafts')}>
        返回草稿列表
      </button>
    </div>
  )
}
