import { ArrowLeftOutlined, FileTextOutlined, InfoCircleOutlined } from '@ant-design/icons'
import { Button, Card, Space, Tabs, Tag, Typography } from 'antd'
import { Link, Outlet, useLocation, useNavigate, useParams } from 'react-router-dom'
import { ErrorState, LoadingState } from '../../components/feedback/state-views'
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

  return (
    <div>
      <Button icon={<ArrowLeftOutlined />} style={{ marginBottom: 16 }}>
        <Link to="/knowledge-bases">返回知识库</Link>
      </Button>

      <Card
        title={
          <Space direction="vertical" size={2}>
            <Typography.Title level={4} style={{ margin: 0 }}>
              {knowledgeBase.name}
            </Typography.Title>
            <Typography.Text type="secondary">
              {knowledgeBase.code} · {knowledgeBase.defaultQueryMode}
            </Typography.Text>
          </Space>
        }
        extra={<Tag>{knowledgeBase.permission}</Tag>}
      >
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
