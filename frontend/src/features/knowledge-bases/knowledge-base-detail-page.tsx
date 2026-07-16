import {
  ArrowLeftOutlined,
  FileTextOutlined,
  InfoCircleOutlined,
} from '@ant-design/icons'
import { Button, Card, Space, Tabs, Tag, Typography } from 'antd'
import { Link, Outlet, useLocation, useNavigate, useParams } from 'react-router-dom'
import { ErrorState, LoadingState } from '../../components/feedback/state-views'
import {
  permissionColor,
  permissionLabel,
  queryModeLabel,
  statusColor,
  statusLabel,
} from './labels'
import { useKnowledgeBaseQuery } from './queries'

export function KnowledgeBaseDetailPage() {
  const { kbId = '' } = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const query = useKnowledgeBaseQuery(kbId)

  if (query.isPending) return <LoadingState message="正在加载知识库详情…" />
  if (query.isError) {
    return (
      <ErrorState
        error={query.error}
        onRetry={() => void query.refetch()}
        title="知识库详情加载失败"
      />
    )
  }

  const knowledgeBase = query.data
  const documentsActive = location.pathname.endsWith('/documents')
  const statusText = statusLabel[knowledgeBase.status] ?? knowledgeBase.status

  return (
    <div>
      <div className="page-header" style={{ marginBottom: 16 }}>
        <div style={{ minWidth: 0 }}>
          <Button
            type="link"
            icon={<ArrowLeftOutlined />}
            style={{ paddingInline: 0, height: 'auto', marginBottom: 8 }}
            onClick={() => navigate('/knowledge-bases')}
          >
            返回知识库
          </Button>
          <h2 style={{ margin: 0 }}>{knowledgeBase.name}</h2>
          <Space wrap size={[8, 4]} style={{ marginTop: 8 }}>
            <Typography.Text type="secondary" code>
              {knowledgeBase.code}
            </Typography.Text>
            <Tag color={permissionColor[knowledgeBase.permission]}>
              {permissionLabel[knowledgeBase.permission]}
            </Tag>
            <Tag color={statusColor[knowledgeBase.status] ?? 'default'}>{statusText}</Tag>
            <Tag>
              检索 · {queryModeLabel[knowledgeBase.defaultQueryMode] ?? knowledgeBase.defaultQueryMode}
            </Tag>
            <Tag>
              <FileTextOutlined aria-hidden style={{ marginRight: 4 }} />
              {knowledgeBase.documentCount} 份文档
            </Tag>
          </Space>
          {knowledgeBase.description ? (
            <Typography.Paragraph
              type="secondary"
              ellipsis={{ rows: 2, tooltip: knowledgeBase.description }}
              style={{ marginBottom: 0, marginTop: 10, maxWidth: 720 }}
            >
              {knowledgeBase.description}
            </Typography.Paragraph>
          ) : null}
        </div>
        <Button type="default">
          <Link to={`/knowledge-bases/${knowledgeBase.id}/documents`}>管理文档</Link>
        </Button>
      </div>

      <Card styles={{ body: { paddingTop: 8 } }}>
        <Tabs
          activeKey={documentsActive ? 'documents' : 'overview'}
          onChange={(value) => {
            const base = `/knowledge-bases/${knowledgeBase.id}`
            navigate(value === 'documents' ? `${base}/documents` : base)
          }}
          items={[
            {
              key: 'overview',
              label: (
                <span>
                  <InfoCircleOutlined /> 概览
                </span>
              ),
            },
            {
              key: 'documents',
              label: (
                <span>
                  <FileTextOutlined /> 文档
                </span>
              ),
            },
          ]}
        />
        <Outlet context={{ knowledgeBase }} />
      </Card>
    </div>
  )
}
