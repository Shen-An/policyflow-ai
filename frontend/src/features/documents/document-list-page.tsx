import { EyeOutlined, ReloadOutlined, UploadOutlined } from '@ant-design/icons'
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useState } from 'react'
import { useOutletContext, useSearchParams } from 'react-router-dom'
import type {
  KnowledgeBase,
  KnowledgeDocument,
  ResourcePermission,
} from '../../api/knowledge-bases'
import { LoadingState } from '../../components/feedback/state-views'
import { UploadDocumentDialog } from './components/upload-document-dialog'
import {
  useDocumentDetailQuery,
  useDocumentsQuery,
  useDocumentStatusQuery,
  useReindexDocumentMutation,
} from './queries'

function positiveInt(value: string | null, fallback: number): number {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback
}

function canWrite(permission: ResourcePermission): boolean {
  return permission === 'write' || permission === 'admin'
}

function statusColor(status: string): string {
  if (status === 'ready' || status === 'indexed') return 'success'
  if (status === 'failed') return 'error'
  if (status === 'processing' || status === 'pending' || status === 'indexing') return 'processing'
  return 'default'
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

export function DocumentListPage() {
  const { knowledgeBase } = useOutletContext<{ knowledgeBase: KnowledgeBase }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const [uploadOpen, setUploadOpen] = useState(false)
  const [selectedDocumentId, setSelectedDocumentId] = useState('')
  const page = positiveInt(searchParams.get('page'), 1)
  const pageSize = Math.min(positiveInt(searchParams.get('page_size'), 20), 100)
  const query = useDocumentsQuery(knowledgeBase.id, page, pageSize)
  const writable = canWrite(knowledgeBase.permission)

  const columns: ColumnsType<KnowledgeDocument> = [
    {
      title: '标题',
      dataIndex: 'title',
      render: (title: string, record) => (
        <Button
          type="link"
          style={{ paddingInline: 0, height: 'auto' }}
          onClick={(event) => {
            event.stopPropagation()
            setSelectedDocumentId(record.id)
          }}
        >
          {title}
        </Button>
      ),
    },
    { title: '类型', dataIndex: 'fileType', width: 100 },
    { title: '版本', dataIndex: 'sourceVersion', width: 80 },
    {
      title: '索引状态',
      dataIndex: 'indexStatus',
      width: 140,
      render: (value: string, record) => (
        <DocumentStatus documentId={record.id} status={value} />
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      width: 180,
      render: (value: string) => formatDate(value),
    },
    {
      title: '操作',
      key: 'actions',
      width: 200,
      render: (_, document) => (
        <Space size={4} onClick={(event) => event.stopPropagation()}>
          <Button
            size="small"
            type="link"
            onClick={() => setSelectedDocumentId(document.id)}
          >
            <EyeOutlined aria-hidden />
            查看
          </Button>
          {writable ? (
            <ReindexButton knowledgeBaseId={knowledgeBase.id} documentId={document.id} />
          ) : null}
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          gap: 12,
          marginBottom: 16,
          flexWrap: 'wrap',
        }}
      >
        <div>
          <Typography.Title level={5} style={{ margin: 0 }}>
            文档
          </Typography.Title>
          <Typography.Text type="secondary">
            点击标题或“查看”可阅读文档正文；索引任务会在成功或失败时停止轮询。
          </Typography.Text>
        </div>
        {writable ? (
          <Button type="primary" onClick={() => setUploadOpen(true)}>
            <UploadOutlined aria-hidden />
            上传文档
          </Button>
        ) : (
          <Typography.Text type="secondary">
            当前为只读权限，不能上传或重新索引。
          </Typography.Text>
        )}
      </div>

      {query.isPending ? (
        <LoadingState message="正在加载文档…" minH="min-h-48" />
      ) : query.isError ? (
        <Alert
          type="error"
          showIcon
          message="文档列表加载失败"
          description={query.error.message}
          action={<Button onClick={() => void query.refetch()}>重新加载</Button>}
        />
      ) : (
        <Table
          rowKey="id"
          columns={columns}
          dataSource={query.data?.items ?? []}
          onRow={(record) => ({
            onClick: () => setSelectedDocumentId(record.id),
            style: { cursor: 'pointer' },
          })}
          locale={{
            emptyText: (
              <Empty
                description={
                  <div>
                    <div>还没有文档</div>
                    {writable ? (
                      <div style={{ marginTop: 4, color: '#5b6577' }}>
                        上传第一份制度文档开始索引。
                      </div>
                    ) : null}
                  </div>
                }
              />
            ),
          }}
          pagination={{
            current: page,
            pageSize,
            total: query.data?.total ?? 0,
            showSizeChanger: false,
            showTotal: (total) => `共 ${total} 份`,
            onChange: (nextPage) => {
              const next = new URLSearchParams(searchParams)
              next.set('page', String(nextPage))
              setSearchParams(next)
            },
          }}
        />
      )}

      <UploadDocumentDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        knowledgeBaseId={knowledgeBase.id}
      />

      <DocumentDetailDrawer
        documentId={selectedDocumentId}
        knowledgeBaseName={knowledgeBase.name}
        writable={writable}
        knowledgeBaseId={knowledgeBase.id}
        onClose={() => setSelectedDocumentId('')}
      />
    </div>
  )
}

function DocumentStatus({
  documentId,
  status,
}: {
  documentId: string
  status: string
}) {
  const live = useDocumentStatusQuery(documentId, status)
  const current = live.data?.indexStatus ?? status
  return <Tag color={statusColor(current)}>{current}</Tag>
}

function ReindexButton({
  knowledgeBaseId,
  documentId,
}: {
  knowledgeBaseId: string
  documentId: string
}) {
  const mutation = useReindexDocumentMutation(knowledgeBaseId)
  return (
    <Button
      size="small"
      type="link"
      loading={mutation.isPending}
      onClick={(event) => {
        event.stopPropagation()
        void mutation.mutateAsync(documentId)
      }}
    >
      <ReloadOutlined aria-hidden />
      重新索引
    </Button>
  )
}

function DocumentDetailDrawer({
  documentId,
  knowledgeBaseName,
  knowledgeBaseId,
  writable,
  onClose,
}: {
  documentId: string
  knowledgeBaseName: string
  knowledgeBaseId: string
  writable: boolean
  onClose: () => void
}) {
  const open = Boolean(documentId)
  const query = useDocumentDetailQuery(documentId, open)
  const reindex = useReindexDocumentMutation(knowledgeBaseId)

  return (
    <Drawer
      title={query.data?.title ?? '文档详情'}
      width={Math.min(820, typeof window !== 'undefined' ? window.innerWidth - 48 : 820)}
      open={open}
      onClose={onClose}
      destroyOnHidden
      extra={
        writable && query.data ? (
          <Button
            size="small"
            loading={reindex.isPending}
            onClick={() => void reindex.mutateAsync(documentId)}
          >
            <ReloadOutlined aria-hidden />
            重新索引
          </Button>
        ) : null
      }
    >
      {query.isPending ? (
        <div style={{ display: 'grid', placeItems: 'center', minHeight: 240 }}>
          <Spin tip="正在加载文档内容…" />
        </div>
      ) : query.isError ? (
        <Alert
          type="error"
          showIcon
          message="文档详情加载失败"
          description={query.error.message}
          action={<Button onClick={() => void query.refetch()}>重试</Button>}
        />
      ) : query.data ? (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Descriptions bordered size="small" column={2}>
            <Descriptions.Item label="知识库" span={2}>
              {knowledgeBaseName}
            </Descriptions.Item>
            <Descriptions.Item label="文件类型">{query.data.fileType}</Descriptions.Item>
            <Descriptions.Item label="版本">v{query.data.sourceVersion}</Descriptions.Item>
            <Descriptions.Item label="索引状态">
              <Tag color={statusColor(query.data.indexStatus)}>{query.data.indexStatus}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="正文长度">
              {query.data.contentLength.toLocaleString('zh-CN')} 字符
            </Descriptions.Item>
            <Descriptions.Item label="创建时间">{formatDate(query.data.createdAt)}</Descriptions.Item>
            <Descriptions.Item label="更新时间">{formatDate(query.data.updatedAt)}</Descriptions.Item>
            {query.data.indexError ? (
              <Descriptions.Item label="索引错误" span={2}>
                <Typography.Text type="danger">{query.data.indexError}</Typography.Text>
              </Descriptions.Item>
            ) : null}
          </Descriptions>

          <div>
            <Typography.Title level={5} style={{ marginTop: 0 }}>
              文档正文
            </Typography.Title>
            {query.data.contentText ? (
              <div
                className="document-content-panel"
                style={{
                  maxHeight: 'calc(100vh - 320px)',
                  overflow: 'auto',
                  padding: 16,
                  borderRadius: 12,
                  border: '1px solid #eef2f7',
                  background: 'linear-gradient(180deg, #ffffff 0%, #fbfcfe 100%)',
                  whiteSpace: 'pre-wrap',
                  lineHeight: 1.8,
                  fontSize: 14,
                  color: '#0f1729',
                }}
              >
                {query.data.contentText}
              </div>
            ) : (
              <Empty description="暂无可展示的正文内容" />
            )}
          </div>
        </Space>
      ) : null}
    </Drawer>
  )
}
