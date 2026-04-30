import {
  ArrowRightOutlined,
  BarChartOutlined,
  BookOutlined,
  FileTextOutlined,
  MessageOutlined,
  RiseOutlined,
} from '@ant-design/icons'
import { Card, Col, List, Row, Space, Statistic, Tag, Typography } from 'antd'
import { Link } from 'react-router-dom'

const { Title, Paragraph, Text } = Typography

export function WorkspacePage() {
  const hour = new Date().getHours()
  const greeting =
    hour < 6 ? '夜深了' : hour < 12 ? '早上好' : hour < 18 ? '下午好' : '晚上好'

  return (
    <div>
      <div className="page-header">
        <div>
          <Title level={3} style={{ margin: 0 }}>
            工作台
          </Title>
          <Paragraph type="secondary" style={{ marginBottom: 0 }}>
            {greeting}，这里是你的 PolicyFlow AI 总览。
          </Paragraph>
        </div>
        <Tag color="success">服务运行正常</Tag>
      </div>

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic title="知识库" value={3} prefix={<BookOutlined />} suffix="授权" />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="历史对话"
              value={12}
              prefix={<MessageOutlined />}
              suffix={
                <Text type="success" style={{ fontSize: 12 }}>
                  <RiseOutlined /> +3 本周
                </Text>
              }
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic title="草稿箱" value={2} prefix={<FileTextOutlined />} suffix="待确认" />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic title="评估报告" value={5} prefix={<BarChartOutlined />} suffix="份" />
          </Card>
        </Col>
      </Row>

      <Title level={4} style={{ marginTop: 28, marginBottom: 16 }}>
        快捷入口
      </Title>
      <Row gutter={[16, 16]}>
        {[
          {
            title: '制度问答',
            desc: '向授权知识库提问，获取制度依据与引用溯源。',
            href: '/chat',
            color: '#4f46e5',
            icon: <MessageOutlined />,
          },
          {
            title: '我的草稿',
            desc: '查看正在编辑的政策草案，继续写作或确认发布。',
            href: '/drafts',
            color: '#0f766e',
            icon: <FileTextOutlined />,
          },
          {
            title: '知识库管理',
            desc: '浏览和维护授权知识库，管理文档与标签。',
            href: '/knowledge-bases',
            color: '#1d4ed8',
            icon: <BookOutlined />,
          },
        ].map((item) => (
          <Col xs={24} md={8} key={item.href}>
            <Link to={item.href} style={{ textDecoration: 'none' }}>
              <Card hoverable styles={{ body: { minHeight: 160 } }}>
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  <div
                    style={{
                      width: 40,
                      height: 40,
                      borderRadius: 10,
                      background: `${item.color}14`,
                      color: item.color,
                      display: 'grid',
                      placeItems: 'center',
                      fontSize: 18,
                    }}
                  >
                    {item.icon}
                  </div>
                  <div>
                    <Title level={5} style={{ margin: 0 }}>
                      {item.title}
                    </Title>
                    <Paragraph type="secondary" style={{ marginBottom: 0, marginTop: 6 }}>
                      {item.desc}
                    </Paragraph>
                  </div>
                  <Text type="secondary">
                    进入 <ArrowRightOutlined />
                  </Text>
                </Space>
              </Card>
            </Link>
          </Col>
        ))}
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={12}>
          <Card title="最近活动" extra={<Text type="secondary">最近一次操作记录</Text>}>
            <List
              dataSource={[
                { action: '查询制度', detail: '差旅住宿标准', time: '2 小时前' },
                { action: '编辑草稿', detail: '请假管理办法 v2', time: '昨天' },
                { action: '导入文档', detail: '报销制度.pdf', time: '3 天前' },
                { action: '评估报告', detail: 'Q2 RAG 评估', time: '1 周前' },
              ]}
              renderItem={(item) => (
                <List.Item>
                  <List.Item.Meta title={item.action} description={item.detail} />
                  <Text type="secondary">{item.time}</Text>
                </List.Item>
              )}
            />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="使用提示" extra={<Text type="secondary">让问答更精准的小技巧</Text>}>
            <List
              dataSource={[
                '尽量使用完整的句子描述问题，例如“出差时每天住宿上限是多少？”',
                '在检索范围中勾选相关知识库，缩小检索范围可提高回答精度。',
                '查看引用时，可点击 chunk 跳转到原文段落。',
                '对回答提交反馈（有用/无用/引用错误），有助于持续优化检索质量。',
              ]}
              renderItem={(item, index) => (
                <List.Item>
                  <Space align="start">
                    <Tag color="processing">{index + 1}</Tag>
                    <Text>{item}</Text>
                  </Space>
                </List.Item>
              )}
            />
          </Card>
        </Col>
      </Row>
    </div>
  )
}
