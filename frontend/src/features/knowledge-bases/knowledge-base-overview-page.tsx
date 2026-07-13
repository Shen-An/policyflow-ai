import { Card, Col, Descriptions, Row } from 'antd'
import { useOutletContext } from 'react-router-dom'
import type { KnowledgeBase } from '../../api/knowledge-bases'

export function KnowledgeBaseOverviewPage() {
  const { knowledgeBase } = useOutletContext<{ knowledgeBase: KnowledgeBase }>()

  return (
    <Row gutter={[16, 16]}>
      <Col span={24}>
        <Card size="small" title="基本信息">
          <Descriptions column={{ xs: 1, sm: 2 }} bordered size="small">
            <Descriptions.Item label="描述" span={2}>
              {knowledgeBase.description || '暂无描述'}
            </Descriptions.Item>
            <Descriptions.Item label="状态">{knowledgeBase.status}</Descriptions.Item>
            <Descriptions.Item label="资源权限">{knowledgeBase.permission}</Descriptions.Item>
            <Descriptions.Item label="文档数量">{knowledgeBase.documentCount}</Descriptions.Item>
            <Descriptions.Item label="默认检索模式">
              {knowledgeBase.defaultQueryMode}
            </Descriptions.Item>
            <Descriptions.Item label="部门 ID">{knowledgeBase.departmentId}</Descriptions.Item>
          </Descriptions>
        </Card>
      </Col>
    </Row>
  )
}
