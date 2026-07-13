import { BugOutlined } from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Collapse,
  Col,
  Empty,
  Form,
  Input,
  List,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { EvalRunSummary } from '../../api/eval'
import { LoadingState } from '../../components/feedback/state-views'
import { useKnowledgeBasesQuery } from '../knowledge-bases/queries'
import {
  useCreateEvalCaseMutation,
  useCreateEvalRunMutation,
  useCreateRetrievalItemMutation,
  useEvalCasesQuery,
  useEvalRunQuery,
  useEvalRunsQuery,
  useRetrievalDebugMutation,
  useRetrievalItemsQuery,
} from './queries'

function splitCsv(value: string) {
  return value.split(',').map((item) => item.trim()).filter(Boolean)
}

function statusColor(value: string): string {
  if (value === 'success' || value === 'passed') return 'success'
  if (value === 'failed') return 'error'
  if (value === 'skipped' || value === 'disabled') return 'default'
  if (value === 'running' || value === 'pending') return 'processing'
  return 'default'
}

export function EvaluationPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const runId = searchParams.get('run_id') ?? ''

  return (
    <div>
      <div className="page-header">
        <div>
          <h2>评估中心</h2>
          <p>管理评估用例、运行历史和单次检索调试；skipped/disabled 不按 0 分展示。</p>
        </div>
      </div>
      <Space orientation="vertical" size={16} style={{ width: '100%' }}>
        <DatasetSection />
        <RunSection
          selectedRunId={runId}
          onSelectRun={(id) => {
            const next = new URLSearchParams(searchParams)
            if (id) next.set('run_id', id)
            else next.delete('run_id')
            setSearchParams(next)
          }}
        />
        <RetrievalDebugSection />
      </Space>
    </div>
  )
}

function DatasetSection() {
  const cases = useEvalCasesQuery()
  const items = useRetrievalItemsQuery()
  const createCase = useCreateEvalCaseMutation()
  const createItem = useCreateRetrievalItemMutation()
  const knowledgeBases = useKnowledgeBasesQuery()
  const [caseForm] = Form.useForm()
  const [itemForm] = Form.useForm()

  return (
    <Card title="评估数据集">
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={12}>
          <Card type="inner" title="新增回答评估用例" size="small">
            <Form
              form={caseForm}
              layout="vertical"
              requiredMark={false}
              initialValues={{ category: 'hr' }}
              onFinish={(values: {
                question: string
                category: string
                keywords?: string
                sourceTitles?: string
              }) => {
                createCase.mutate(
                  {
                    question: values.question.trim(),
                    category: values.category.trim(),
                    expectedAnswerKeywords: splitCsv(values.keywords ?? ''),
                    expectedSourceDocuments: splitCsv(values.sourceTitles ?? ''),
                    shouldAnswer: true,
                  },
                  {
                    onSuccess: () => caseForm.resetFields(['question', 'keywords', 'sourceTitles']),
                  },
                )
              }}
            >
              <Form.Item
                label="问题"
                name="question"
                rules={[{ required: true, message: '请输入问题' }]}
              >
                <Input />
              </Form.Item>
              <Form.Item
                label="知识库分类代码"
                name="category"
                rules={[{ required: true, message: '请输入分类' }]}
              >
                <Input />
              </Form.Item>
              <Form.Item label="期望关键词（逗号分隔）" name="keywords">
                <Input />
              </Form.Item>
              <Form.Item label="期望来源标题（逗号分隔）" name="sourceTitles">
                <Input />
              </Form.Item>
              <Button
                type="primary"
                htmlType="submit"
                autoInsertSpace={false}
                loading={createCase.isPending}
              >
                创建用例
              </Button>
              {createCase.isError ? (
                <Alert
                  type="error"
                  showIcon
                  style={{ marginTop: 12 }}
                  title={createCase.error.message}
                />
              ) : null}
            </Form>
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card type="inner" title="新增检索评估用例" size="small">
            <Form
              form={itemForm}
              layout="vertical"
              requiredMark={false}
              onFinish={(values: {
                retrievalQuery: string
                selectedKb: string
                evalCaseId?: string
                relevantDocuments?: string
              }) => {
                createItem.mutate(
                  {
                    evalCaseId: values.evalCaseId || undefined,
                    query: values.retrievalQuery.trim(),
                    knowledgeBaseIds: [values.selectedKb],
                    relevantDocumentIds: splitCsv(values.relevantDocuments ?? ''),
                  },
                  {
                    onSuccess: () =>
                      itemForm.resetFields(['retrievalQuery', 'relevantDocuments']),
                  },
                )
              }}
            >
              <Form.Item
                label="查询"
                name="retrievalQuery"
                rules={[{ required: true, message: '请输入查询' }]}
              >
                <Input />
              </Form.Item>
              <Form.Item
                label="知识库"
                name="selectedKb"
                rules={[{ required: true, message: '请选择知识库' }]}
              >
                <Select
                  placeholder="请选择"
                  options={(knowledgeBases.data ?? []).map((kb) => ({
                    value: kb.id,
                    label: kb.name,
                  }))}
                />
              </Form.Item>
              <Form.Item label="关联回答用例" name="evalCaseId">
                <Select
                  allowClear
                  placeholder="无"
                  options={(cases.data ?? []).map((item) => ({
                    value: item.id,
                    label: item.question,
                  }))}
                />
              </Form.Item>
              <Form.Item label="相关文档 ID（逗号分隔，可空）" name="relevantDocuments">
                <Input />
              </Form.Item>
              <Button
                type="primary"
                htmlType="submit"
                autoInsertSpace={false}
                loading={createItem.isPending}
              >
                创建检索用例
              </Button>
              {createItem.isError ? (
                <Alert
                  type="error"
                  showIcon
                  style={{ marginTop: 12 }}
                  title={createItem.error.message}
                />
              ) : null}
            </Form>
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={12}>
          <Typography.Title level={5} style={{ marginTop: 0 }}>
            回答用例（{cases.data?.length ?? 0}）
          </Typography.Title>
          {cases.isPending ? (
            <LoadingState message="正在加载…" minH="min-h-0" />
          ) : (
            <List
              size="small"
              bordered
              dataSource={cases.data ?? []}
              locale={{ emptyText: <Empty description="暂无用例" /> }}
              renderItem={(item) => (
                <List.Item>
                  {item.enabled ? 'enabled' : 'disabled'} · {item.category} · {item.question}
                </List.Item>
              )}
            />
          )}
        </Col>
        <Col xs={24} lg={12}>
          <Typography.Title level={5} style={{ marginTop: 0 }}>
            检索用例（{items.data?.length ?? 0}）
          </Typography.Title>
          {items.isPending ? (
            <LoadingState message="正在加载…" minH="min-h-0" />
          ) : (
            <List
              size="small"
              bordered
              dataSource={items.data ?? []}
              locale={{ emptyText: <Empty description="暂无用例" /> }}
              renderItem={(item) => (
                <List.Item>
                  {item.enabled ? 'enabled' : 'disabled'} · {item.query}
                </List.Item>
              )}
            />
          )}
        </Col>
      </Row>
    </Card>
  )
}

function RunSection({
  selectedRunId,
  onSelectRun,
}: {
  selectedRunId: string
  onSelectRun: (id: string) => void
}) {
  const cases = useEvalCasesQuery()
  const retrievalItems = useRetrievalItemsQuery()
  const runs = useEvalRunsQuery(1, 20, '')
  const create = useCreateEvalRunMutation()
  const [form] = Form.useForm()

  const columns: ColumnsType<EvalRunSummary> = useMemo(
    () => [
      {
        title: '名称',
        dataIndex: 'name',
        render: (value: string) => <Typography.Text strong>{value}</Typography.Text>,
      },
      {
        title: '状态',
        dataIndex: 'status',
        width: 120,
        render: (value: string) => <Tag color={statusColor(value)}>{value}</Tag>,
      },
      { title: '用例数', dataIndex: 'totalCases', width: 100 },
      {
        title: '创建时间',
        dataIndex: 'createdAt',
        width: 180,
        render: (value: string) => new Date(value).toLocaleString('zh-CN'),
      },
      {
        title: '操作',
        key: 'actions',
        width: 120,
        render: (_, run) => (
          <Button size="small" autoInsertSpace={false} onClick={() => onSelectRun(run.id)}>
            查看结果
          </Button>
        ),
      },
    ],
    [onSelectRun],
  )

  return (
    <Card title="评估 Run">
      <Form
        form={form}
        layout="vertical"
        requiredMark={false}
        initialValues={{ evalTypes: ['retrieval'], caseIds: [], itemIds: [] }}
        onFinish={async (values: {
          name: string
          evalTypes: Array<'retrieval' | 'rag_answer' | 'ragas'>
          caseIds: string[]
          itemIds: string[]
        }) => {
          const run = await create.mutateAsync({
            name: values.name.trim(),
            caseIds: values.caseIds ?? [],
            retrievalItemIds: values.itemIds ?? [],
            evalTypes: values.evalTypes,
            queryMode: 'hybrid',
          })
          form.resetFields(['name'])
          onSelectRun(run.id)
        }}
      >
        <Row gutter={16}>
          <Col xs={24} md={8}>
            <Form.Item
              label="Run 名称"
              name="name"
              rules={[{ required: true, message: '请输入名称' }]}
            >
              <Input />
            </Form.Item>
          </Col>
          <Col xs={24} md={16}>
            <Form.Item label="评估类型" name="evalTypes">
              <Checkbox.Group
                options={[
                  { value: 'retrieval', label: '检索' },
                  { value: 'rag_answer', label: '回答' },
                  { value: 'ragas', label: 'RAGAS' },
                ]}
              />
            </Form.Item>
          </Col>
          <Col xs={24} md={12}>
            <Form.Item label="回答用例" name="caseIds">
              <Checkbox.Group
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  maxHeight: 128,
                  overflow: 'auto',
                  gap: 8,
                }}
                options={(cases.data ?? []).map((item) => ({
                  value: item.id,
                  label: item.question,
                }))}
              />
            </Form.Item>
          </Col>
          <Col xs={24} md={12}>
            <Form.Item label="检索用例" name="itemIds">
              <Checkbox.Group
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  maxHeight: 128,
                  overflow: 'auto',
                  gap: 8,
                }}
                options={(retrievalItems.data ?? []).map((item) => ({
                  value: item.id,
                  label: item.query,
                }))}
              />
            </Form.Item>
          </Col>
        </Row>
        <Button
          type="primary"
          htmlType="submit"
          autoInsertSpace={false}
          loading={create.isPending}
        >
          启动评估
        </Button>
        {create.isError ? (
          <Alert type="error" showIcon style={{ marginTop: 12 }} title={create.error.message} />
        ) : null}
      </Form>

      <Table
        style={{ marginTop: 16 }}
        rowKey="id"
        loading={runs.isPending}
        columns={columns}
        dataSource={runs.data?.items ?? []}
        pagination={false}
        locale={{ emptyText: <Empty description="暂无评估 Run" /> }}
      />

      {selectedRunId ? (
        <RunDetail id={selectedRunId} onClose={() => onSelectRun('')} />
      ) : null}
    </Card>
  )
}

function RunDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const query = useEvalRunQuery(id)

  if (query.isPending) {
    return (
      <div style={{ marginTop: 16 }}>
        <LoadingState message="正在加载 Run…" minH="min-h-0" />
      </div>
    )
  }
  if (query.isError) {
    return <Alert type="error" showIcon style={{ marginTop: 16 }} message={query.error.message} />
  }

  const run = query.data

  return (
    <Card
      size="small"
      style={{ marginTop: 16 }}
      title={run.name}
      extra={
        <Button size="small" autoInsertSpace={false} onClick={onClose}>
          关闭
        </Button>
      }
    >
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        Request ID：{run.requestId ?? '无'}
      </Typography.Text>
      <Space wrap style={{ marginTop: 12, marginBottom: 12 }}>
        <Tag color={statusColor(run.status)}>{run.status}</Tag>
        {Object.entries(run.metrics).map(([key, value]) => (
          <Tag key={key}>
            {key}: {String(value)}
          </Tag>
        ))}
      </Space>
      {run.errorSummary ? (
        <Alert type="error" showIcon style={{ marginBottom: 12 }} title={run.errorSummary} />
      ) : null}
      <Collapse
        items={[
          {
            key: 'config',
            label: '配置快照',
            children: (
              <pre style={{ margin: 0, overflow: 'auto', fontSize: 12 }}>
                {JSON.stringify(run.configSnapshot, null, 2)}
              </pre>
            ),
          },
        ]}
      />
      <Space orientation="vertical" size={12} style={{ width: '100%', marginTop: 16 }}>
        {run.results.map((result) => (
          <Card key={result.id} size="small" type="inner">
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
              <Typography.Text strong>{result.question}</Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {Object.entries(result.typeStatuses)
                  .map(([type, status]) => `${type}:${status}`)
                  .join(' · ')}
              </Typography.Text>
            </div>
            {result.answer ? (
              <Typography.Paragraph style={{ marginTop: 8, marginBottom: 0 }}>
                {result.answer}
              </Typography.Paragraph>
            ) : null}
            {result.errorMessage ? (
              <Typography.Text type="danger" style={{ display: 'block', marginTop: 8 }}>
                {result.errorMessage}
              </Typography.Text>
            ) : null}
            <MetricBlock title="检索指标" value={result.retrievalMetrics} />
            <MetricBlock title="回答指标" value={result.answerMetrics} />
            <MetricBlock title="RAGAS" value={result.ragasMetrics} />
          </Card>
        ))}
      </Space>
    </Card>
  )
}

function RetrievalDebugSection() {
  const mutation = useRetrievalDebugMutation()
  const knowledgeBases = useKnowledgeBasesQuery()
  const [form] = Form.useForm()

  const columns: ColumnsType<Record<string, unknown>> = [
    {
      title: 'Rank',
      dataIndex: 'rank',
      width: 80,
      render: (value) => String(value ?? ''),
    },
    {
      title: 'Retriever',
      dataIndex: 'retriever_type',
      width: 120,
      render: (value) => String(value ?? ''),
    },
    {
      title: 'Document / Chunk',
      key: 'doc',
      render: (_, item) => (
        <div>
          {String(item.document_title ?? item.document_id ?? '—')}
          <br />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {String(item.chunk_id ?? '')}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: 'Score',
      dataIndex: 'score',
      width: 100,
      render: (value) => String(value ?? '—'),
    },
    {
      title: 'Rerank',
      dataIndex: 'rerank_score',
      width: 100,
      render: (value) => String(value ?? '—'),
    },
    {
      title: 'Snippet',
      dataIndex: 'snippet',
      ellipsis: true,
      render: (value) => String(value ?? ''),
    },
  ]

  return (
    <Card
      title={
        <Space>
          <BugOutlined />
          检索调试
        </Space>
      }
    >
      <Form
        form={form}
        layout="inline"
        style={{ rowGap: 12 }}
        onFinish={(values: { query: string; kbId: string }) => {
          mutation.mutate({
            query: values.query.trim(),
            knowledgeBaseIds: [values.kbId],
            queryMode: 'hybrid',
          })
        }}
      >
        <Form.Item
          name="query"
          rules={[{ required: true, message: '请输入查询' }]}
          style={{ flex: 1, minWidth: 220 }}
        >
          <Input aria-label="调试查询" placeholder="输入查询内容" />
        </Form.Item>
        <Form.Item
          name="kbId"
          rules={[{ required: true, message: '请选择知识库' }]}
          style={{ minWidth: 200 }}
        >
          <Select
            placeholder="请选择知识库"
            options={(knowledgeBases.data ?? []).map((kb) => ({
              value: kb.id,
              label: kb.name,
            }))}
          />
        </Form.Item>
        <Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            autoInsertSpace={false}
            loading={mutation.isPending}
          >
            运行调试
          </Button>
        </Form.Item>
      </Form>

      {mutation.isError ? (
        <Alert type="error" showIcon style={{ marginTop: 12 }} title={mutation.error.message} />
      ) : null}

      {mutation.data ? (
        <Table
          style={{ marginTop: 16 }}
          rowKey={(_, index) => String(index)}
          columns={columns}
          dataSource={mutation.data.items}
          pagination={false}
          size="small"
        />
      ) : null}
    </Card>
  )
}

function MetricBlock({
  title,
  value,
}: {
  title: string
  value: Record<string, unknown> | null
}) {
  if (!value) return null
  const status = typeof value.status === 'string' ? value.status : null
  return (
    <div style={{ marginTop: 8, fontSize: 12 }}>
      <Typography.Text strong>{title}：</Typography.Text>
      {status === 'skipped' || status === 'disabled' ? (
        <span>
          {status}（{String(value.reason ?? '无原因')}）
        </span>
      ) : (
        <span>
          {Object.entries(value)
            .map(([key, item]) => `${key}=${String(item)}`)
            .join(' · ')}
        </span>
      )}
    </div>
  )
}
