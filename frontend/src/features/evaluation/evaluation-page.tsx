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
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { EvalResult, EvalRunSummary } from '../../api/eval'
import { LoadingState } from '../../components/feedback/state-views'
import { useKnowledgeBasesQuery } from '../knowledge-bases/queries'
import {
  useCreateEvalCaseMutation,
  useCreateEvalRunMutation,
  useCreateRetrievalItemMutation,
  useEvalCasesQuery,
  useEvalRunQuery,
  useEvalRunsQuery,
  useImportCrudDatasetMutation,
  useRetrievalDebugMutation,
  useRetrievalItemsQuery,
} from './queries'

function splitCsv(value: string) {
  return value.split(',').map((item) => item.trim()).filter(Boolean)
}

function pickRandomIds(ids: string[], count: number): string[] {
  if (count <= 0 || ids.length === 0) return []
  if (count >= ids.length) return [...ids]
  const copy = [...ids]
  for (let i = copy.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1))
    const tmp = copy[i]
    copy[i] = copy[j]
    copy[j] = tmp
  }
  return copy.slice(0, count)
}

function statusColor(value: string): string {
  if (value === 'success' || value === 'passed') return 'success'
  if (value === 'failed') return 'error'
  if (value === 'skipped' || value === 'disabled') return 'default'
  if (value === 'running' || value === 'pending') return 'processing'
  return 'default'
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** Resume-friendly percent, e.g. 0.875 -> 87.5% */
function formatRate(value: unknown, digits = 1): string {
  const num = asNumber(value)
  if (num === null) return '—'
  return `${(num * 100).toFixed(digits)}%`
}

function formatMrr(value: unknown): string {
  const num = asNumber(value)
  if (num === null) return '—'
  return num.toFixed(4)
}

function pickMetric(
  metrics: Record<string, unknown> | null | undefined,
  key: string,
): number | null {
  if (!metrics) return null
  return asNumber(metrics[key])
}

function coreRetrievalMetrics(metrics: Record<string, unknown> | null | undefined) {
  return {
    mrr: pickMetric(metrics, 'mrr'),
    hit1: pickMetric(metrics, 'hit_at_1'),
    hit3: pickMetric(metrics, 'hit_at_3'),
    hit5: pickMetric(metrics, 'hit_at_5') ?? pickMetric(metrics, 'hit_at_3'),
    hit10: pickMetric(metrics, 'hit_at_10'),
    cases: pickMetric(metrics, 'completed_cases') ?? pickMetric(metrics, 'total_cases'),
  }
}

function strategyLabel(raw: unknown): string {
  const value = String(raw ?? '').trim()
  if (!value) return '未知策略'
  if (value === 'hybrid_lightrag_bm25') return 'Hybrid(LightRAG+BM25)'
  if (value === 'lightrag_only') return 'LightRAG'
  if (value === 'bm25_only') return 'BM25'
  return value
}

function extractRunStrategyInfo(configSnapshot: Record<string, unknown> | null | undefined) {
  const retrievalConfig =
    configSnapshot &&
    typeof configSnapshot.retrieval_config === 'object' &&
    configSnapshot.retrieval_config !== null
      ? (configSnapshot.retrieval_config as Record<string, unknown>)
      : {}
  const primary = strategyLabel(retrievalConfig.strategy ?? 'hybrid_lightrag_bm25')
  const compareRaw = Array.isArray(configSnapshot?.compare_strategies)
    ? (configSnapshot?.compare_strategies as unknown[])
    : []
  const compare = compareRaw
    .map((item) => strategyLabel(item))
    .filter((item) => item && item !== primary)
  const rerankEnabled = Boolean(retrievalConfig.rerank_enabled)
  return {
    primary,
    compare,
    rerankEnabled,
    summary:
      compare.length > 0
        ? `${primary}（对比：${compare.join(' / ')}${rerankEnabled ? '；+本地重排' : ''}）`
        : `${primary}${rerankEnabled ? ' + 本地重排' : ''}`,
  }
}

export function EvaluationPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const runId = searchParams.get('run_id') ?? ''

  return (
    <div>
      <div className="page-header">
        <div>
          <h2>评估中心</h2>
          <p>
            默认只看两件事：1）导入测试语料；2）跑检索评估拿到
            <strong> Hit@1 / Hit@5 / MRR</strong>
            。高级配置与逐条结果默认折叠。
          </p>
        </div>
      </div>
      <Space orientation="vertical" size={16} style={{ width: '100%' }}>
        <CrudImportSection />
        <RunSection
          selectedRunId={runId}
          onSelectRun={(id) => {
            const next = new URLSearchParams(searchParams)
            if (id) next.set('run_id', id)
            else next.delete('run_id')
            setSearchParams(next)
          }}
        />
        <Collapse
          items={[
            {
              key: 'dataset',
              label: '高级：手工管理评估用例（通常不用）',
              children: <DatasetSection />,
            },
            {
              key: 'debug',
              label: '高级：单次检索调试（通常不用）',
              children: <RetrievalDebugSection />,
            },
          ]}
        />
      </Space>
    </div>
  )
}

function CrudImportSection() {
  const knowledgeBases = useKnowledgeBasesQuery()
  const importMutation = useImportCrudDatasetMutation()
  const [form] = Form.useForm()

  const evalTestKb = (knowledgeBases.data ?? []).find(
    (kb) => kb.code === 'eval_test' || kb.name === '测试库',
  )

  useEffect(() => {
    if (evalTestKb?.id && !form.getFieldValue('knowledgeBaseId')) {
      form.setFieldValue('knowledgeBaseId', evalTestKb.id)
    }
  }, [evalTestKb?.id, form])

  return (
    <Card title="CRUD 数据集导入（Hit@K / MRR）">
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        从 CRUD-RAG 的 <code>questanswer_*</code> 任务导入语料与金标检索用例。
        <strong>默认导入到专用「测试库」(code=eval_test)</strong>，避免污染业务知识库。
        默认数据路径：
        <code> D:\Coding\Code\Github\CRUD_RAG\data\crud_split\split_merged.json</code>
        。建议先小样本（20–50）；勾选「导入后建立索引」时索引在<strong>后台</strong>执行，页面不会一直转圈。
      </Typography.Paragraph>
      <Form
        form={form}
        layout="vertical"
        requiredMark={false}
        initialValues={{
          knowledgeBaseId: evalTestKb?.id,
          taskType: 'questanswer_1doc',
          sampleSize: 50,
          distractorCount: 200,
          indexDocuments: true,
          createEvalCases: true,
        }}
        onFinish={(values: {
          knowledgeBaseId?: string
          sourcePath?: string
          taskType: 'questanswer_1doc' | 'questanswer_2docs' | 'questanswer_3docs'
          sampleSize: number
          distractorCount: number
          indexDocuments: boolean
          createEvalCases: boolean
        }) => {
          // Always prefer dedicated sandbox. If user left default/old deleted id,
          // omit id so backend creates/revives eval_test.
          const selected = values.knowledgeBaseId || undefined
          const selectedKb = (knowledgeBases.data ?? []).find((kb) => kb.id === selected)
          const useSandbox =
            !selected ||
            selectedKb?.code === 'eval_test' ||
            selectedKb?.name === '测试库'
          importMutation.mutate({
            knowledgeBaseId: useSandbox ? undefined : selected,
            sourcePath: values.sourcePath?.trim() || undefined,
            taskType: values.taskType,
            sampleSize: Number(values.sampleSize) || 50,
            distractorCount: Number(values.distractorCount) || 200,
            indexDocuments: values.indexDocuments,
            createEvalCases: values.createEvalCases,
            useEvalTestKb: true,
          })
        }}
      >
        <Row gutter={16}>
          <Col xs={24} md={8}>
            <Form.Item
              label="目标知识库（默认：测试库）"
              name="knowledgeBaseId"
              extra="留空则自动使用/创建 code=eval_test 的测试库"
            >
              <Select
                allowClear
                placeholder={evalTestKb ? `默认 ${evalTestKb.name}` : '默认自动创建「测试库」'}
                options={(knowledgeBases.data ?? []).map((kb) => ({
                  value: kb.id,
                  label:
                    kb.code === 'eval_test' || kb.name === '测试库'
                      ? `${kb.name}（推荐·评估沙箱）`
                      : `${kb.name}（${kb.code}）`,
                }))}
              />
            </Form.Item>
          </Col>
          <Col xs={24} md={8}>
            <Form.Item label="任务类型" name="taskType">
              <Select
                options={[
                  { value: 'questanswer_1doc', label: 'questanswer_1doc（主评测）' },
                  { value: 'questanswer_2docs', label: 'questanswer_2docs' },
                  { value: 'questanswer_3docs', label: 'questanswer_3docs' },
                ]}
              />
            </Form.Item>
          </Col>
          <Col xs={24} md={8}>
            <Form.Item
              label="评测问题数"
              name="sampleSize"
              extra="建议 30–100"
            >
              <Input type="number" min={1} max={2000} />
            </Form.Item>
          </Col>
          <Col xs={24} md={8}>
            <Form.Item
              label="干扰文档数"
              name="distractorCount"
              extra="防止小库虚高满分；建议 ≥200"
            >
              <Input type="number" min={0} max={5000} />
            </Form.Item>
          </Col>
          <Col xs={24} md={16}>
            <Form.Item label="数据集路径（可选）" name="sourcePath">
              <Input placeholder="默认 split_merged.json；可填绝对路径" />
            </Form.Item>
          </Col>
          <Col xs={24} md={12}>
            <Form.Item
              name="indexDocuments"
              valuePropName="checked"
              extra="索引在后台排队，导入请求会很快返回；可到「测试库 → 文档」查看索引状态。"
            >
              <Checkbox>导入后后台建立索引（推荐）</Checkbox>
            </Form.Item>
          </Col>
          <Col xs={24} md={12}>
            <Form.Item name="createEvalCases" valuePropName="checked">
              <Checkbox>同时创建回答评估用例</Checkbox>
            </Form.Item>
          </Col>
        </Row>
        {importMutation.isPending ? (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            title="正在导入…"
            description="正在写入测试库与评估用例。若勾选了索引，索引会在后台继续，不会卡住本页。"
          />
        ) : null}
        <Button
          type="primary"
          htmlType="submit"
          autoInsertSpace={false}
          loading={importMutation.isPending}
        >
          {importMutation.isPending ? '导入中…' : '导入到测试库'}
        </Button>
        {importMutation.isError ? (
          <Alert
            type="error"
            showIcon
            style={{ marginTop: 12 }}
            title={importMutation.error.message}
            description="若提示超时，请把采样条数降到 20–30，或取消勾选索引后先导入、再在文档页逐个/批量重索引。"
          />
        ) : null}
        {importMutation.isSuccess ? (
          <Alert
            type={importMutation.data.warning ? 'warning' : 'success'}
            showIcon
            style={{ marginTop: 12 }}
            title={`导入完成：评测问 +${importMutation.data.retrievalItemsCreated}，文档 +${importMutation.data.documentsCreated}（干扰 ${importMutation.data.distractorDocumentsCreated}，复用 ${importMutation.data.documentsReused}），语料总量 ${importMutation.data.corpusDocumentCount}${importMutation.data.indexQueued ? `，后台索引排队 ${importMutation.data.indexQueued}` : ''}`}
            description={
              importMutation.data.warning ||
              `目标 KB：${importMutation.data.knowledgeBaseId}。索引完成后启动 retrieval Run，查看 Hit@K / MRR。`
            }
          />
        ) : null}
      </Form>
    </Card>
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
    <div>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        这里只在需要手工补用例时使用。日常请用上方「CRUD 导入」，不要在这里逐条堆数据。
      </Typography.Paragraph>
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={12}>
          <Card type="inner" title="新增回答评估用例" size="small">
            <Form
              form={caseForm}
              layout="vertical"
              requiredMark={false}
              initialValues={{ category: 'eval_test' }}
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

      <Collapse
        style={{ marginTop: 16 }}
        items={[
          {
            key: 'lists',
            label: `展开查看已有用例（回答 ${cases.data?.length ?? 0} / 检索 ${items.data?.length ?? 0}，列表最多展示 50 条）`,
            children: (
              <Row gutter={[16, 16]}>
                <Col xs={24} lg={12}>
                  <List
                    size="small"
                    bordered
                    header="回答用例"
                    dataSource={(cases.data ?? []).slice(0, 50)}
                    locale={{ emptyText: <Empty description="暂无用例" /> }}
                    renderItem={(item) => (
                      <List.Item>
                        {item.enabled ? 'enabled' : 'disabled'} · {item.category} · {item.question}
                      </List.Item>
                    )}
                  />
                </Col>
                <Col xs={24} lg={12}>
                  <List
                    size="small"
                    bordered
                    header="检索用例"
                    dataSource={(items.data ?? []).slice(0, 50)}
                    locale={{ emptyText: <Empty description="暂无用例" /> }}
                    renderItem={(item) => (
                      <List.Item>
                        {item.enabled ? 'enabled' : 'disabled'} · {item.query}
                      </List.Item>
                    )}
                  />
                </Col>
              </Row>
            ),
          },
        ]}
      />
    </div>
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
  const [customSampleSize, setCustomSampleSize] = useState('50')
  const selectedItemCount =
    ((Form.useWatch('itemIds', form) as string[] | undefined) ?? []).length

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
        width: 100,
        render: (value: string) => <Tag color={statusColor(value)}>{value}</Tag>,
      },
      {
        title: 'Hit@1',
        key: 'hit1',
        width: 90,
        render: (_, run) => formatRate(coreRetrievalMetrics(run.metrics).hit1),
      },
      {
        title: 'Hit@5',
        key: 'hit5',
        width: 90,
        render: (_, run) => formatRate(coreRetrievalMetrics(run.metrics).hit5),
      },
      {
        title: 'MRR',
        key: 'mrr',
        width: 100,
        render: (_, run) => formatMrr(coreRetrievalMetrics(run.metrics).mrr),
      },
      { title: '用例', dataIndex: 'totalCases', width: 80 },
      {
        title: '创建时间',
        dataIndex: 'createdAt',
        width: 170,
        render: (value: string) => new Date(value).toLocaleString('zh-CN'),
      },
      {
        title: '操作',
        key: 'actions',
        width: 110,
        render: (_, run) => (
          <Button size="small" autoInsertSpace={false} onClick={() => onSelectRun(run.id)}>
            查看分数
          </Button>
        ),
      },
    ],
    [onSelectRun],
  )

  return (
    <Card title="评估 Run（主看 Hit@K / MRR）">
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        只做检索量化：评估类型勾「检索」→ 点「随机 50/100」→ 启动。完成后看 Hit@1 / Hit@5 / MRR。
      </Typography.Paragraph>
      <Form
        form={form}
        layout="vertical"
        requiredMark={false}
        initialValues={{
          evalTypes: ['retrieval'],
          caseIds: [],
          itemIds: [],
          strategy: 'hybrid_lightrag_bm25',
          compareStrategies: [],
          rerankEnabled: false,
        }}
        onFinish={async (values: {
          name: string
          evalTypes: Array<'retrieval' | 'rag_answer' | 'ragas'>
          caseIds: string[]
          itemIds: string[]
          strategy: string
          compareStrategies: string[]
          rerankEnabled: boolean
        }) => {
          const evalTypes = values.evalTypes ?? []
          const caseIds = values.caseIds ?? []
          const itemIds = values.itemIds ?? []
          const compareStrategies = values.compareStrategies ?? []

          // Client-side guard with Chinese messages (backend also validates).
          if (!evalTypes.length) {
            throw new Error('请至少勾选一种评估类型（检索评估请勾「检索」）。')
          }
          if (evalTypes.includes('retrieval') && itemIds.length === 0) {
            throw new Error(
              '检索评估必须勾选至少一个「检索用例」。请先导入 CRUD 到测试库，再在下方勾选检索用例。',
            )
          }
          if (
            (evalTypes.includes('rag_answer') || evalTypes.includes('ragas')) &&
            caseIds.length === 0
          ) {
            throw new Error(
              '勾选「回答」或「RAGAS」时需要回答用例。若只做 Hit@K/MRR，请只勾「检索」。',
            )
          }
          if (compareStrategies.length > 0 && !evalTypes.includes('retrieval')) {
            throw new Error('多策略对比需要勾选「检索」评估类型。')
          }
          if (itemIds.length === 0 && caseIds.length === 0) {
            throw new Error('请至少选择一个检索用例或回答用例。')
          }

          const run = await create.mutateAsync({
            name: values.name.trim(),
            caseIds,
            retrievalItemIds: itemIds,
            evalTypes,
            queryMode: 'hybrid',
            strategy: values.strategy,
            compareStrategies,
            ragasEnabled: evalTypes.includes('ragas'),
            rerankEnabled: values.rerankEnabled,
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
              <Input placeholder="例如：Hybrid-vs-BM25-N50" />
            </Form.Item>
          </Col>
          <Col xs={24} md={8}>
            <Form.Item label="主策略" name="strategy">
              <Select
                options={[
                  { value: 'hybrid_lightrag_bm25', label: 'Hybrid (LightRAG+BM25)' },
                  { value: 'lightrag_only', label: 'LightRAG only' },
                  { value: 'bm25_only', label: 'BM25 only' },
                ]}
              />
            </Form.Item>
          </Col>
          <Col xs={24} md={8}>
            <Form.Item
              label="评估类型"
              name="evalTypes"
              extra="简历量化主指标请只勾「检索」"
            >
              <Checkbox.Group
                options={[
                  { value: 'retrieval', label: '检索（Hit@K / MRR）' },
                  { value: 'rag_answer', label: '回答' },
                  { value: 'ragas', label: 'RAGAS' },
                ]}
              />
            </Form.Item>
          </Col>
          <Col xs={24}>
            <Form.Item
              label="多策略对比（可选，会按每个策略重跑检索用例）"
              name="compareStrategies"
            >
              <Checkbox.Group
                options={[
                  { value: 'hybrid_lightrag_bm25', label: 'Hybrid' },
                  { value: 'lightrag_only', label: 'LightRAG' },
                  { value: 'bm25_only', label: 'BM25' },
                ]}
              />
            </Form.Item>
          </Col>
          <Col xs={24}>
            <Form.Item name="rerankEnabled" valuePropName="checked">
              <Checkbox>
                启用本地重排（lexical fusion，非 cross-encoder；metadata 含 rerank_method）
              </Checkbox>
            </Form.Item>
          </Col>
          <Col xs={24} md={12}>
            <Form.Item
              label={`回答用例（${cases.data?.length ?? 0}）`}
              name="caseIds"
              extra="仅当勾选「回答/RAGAS」时需要"
            >
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
            <Form.Item
              label={`检索用例（${retrievalItems.data?.length ?? 0}）`}
              name="itemIds"
              extra="Hit@K/MRR 必选。建议随机 50/100，不要全选几百条。"
              rules={[
                {
                  validator: async (_, value) => {
                    const types = form.getFieldValue('evalTypes') as string[] | undefined
                    if (types?.includes('retrieval') && (!value || value.length === 0)) {
                      return Promise.reject(new Error('请勾选至少一个检索用例'))
                    }
                    return Promise.resolve()
                  },
                },
              ]}
            >
              <Checkbox.Group
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  maxHeight: 160,
                  overflow: 'auto',
                  gap: 8,
                }}
                options={(retrievalItems.data ?? []).map((item) => ({
                  value: item.id,
                  label: item.query,
                }))}
              />
            </Form.Item>
            <Space wrap style={{ marginBottom: 12 }}>
              <Button
                size="small"
                type="primary"
                disabled={!retrievalItems.data?.length}
                onClick={() => {
                  const ids = (retrievalItems.data ?? []).map((item) => item.id)
                  form.setFieldValue('itemIds', pickRandomIds(ids, 50))
                }}
              >
                随机 50
              </Button>
              <Button
                size="small"
                type="primary"
                disabled={!retrievalItems.data?.length}
                onClick={() => {
                  const ids = (retrievalItems.data ?? []).map((item) => item.id)
                  form.setFieldValue('itemIds', pickRandomIds(ids, 100))
                }}
              >
                随机 100
              </Button>
              <Space.Compact>
                <Input
                  size="small"
                  type="number"
                  min={1}
                  placeholder="自定义 N"
                  style={{ width: 96 }}
                  value={customSampleSize}
                  onChange={(event) => setCustomSampleSize(event.target.value)}
                />
                <Button
                  size="small"
                  disabled={!retrievalItems.data?.length}
                  onClick={() => {
                    const n = Math.max(1, Number(customSampleSize) || 0)
                    if (!n) return
                    const ids = (retrievalItems.data ?? []).map((item) => item.id)
                    form.setFieldValue('itemIds', pickRandomIds(ids, n))
                  }}
                >
                  随机 N
                </Button>
              </Space.Compact>
              <Button
                size="small"
                disabled={!retrievalItems.data?.length}
                onClick={() =>
                  form.setFieldValue(
                    'itemIds',
                    (retrievalItems.data ?? []).map((item) => item.id),
                  )
                }
              >
                全选
              </Button>
              <Button size="small" onClick={() => form.setFieldValue('itemIds', [])}>
                清空
              </Button>
              <Typography.Text type="secondary">
                已选 {selectedItemCount} 条
              </Typography.Text>
            </Space>
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
          <Alert
            type="error"
            showIcon
            style={{ marginTop: 12 }}
            title={create.error.message}
            description="若只做检索量化：评估类型仅勾「检索」，并至少勾选一个检索用例。"
          />
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
  const [exporting, setExporting] = useState<'json' | 'csv' | null>(null)

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
  const core = coreRetrievalMetrics(run.metrics)
  const strategyInfo = extractRunStrategyInfo(run.configSnapshot)
  const strategyComparison =
    run.metrics.strategy_comparison &&
    typeof run.metrics.strategy_comparison === 'object' &&
    !Array.isArray(run.metrics.strategy_comparison)
      ? (run.metrics.strategy_comparison as Record<string, Record<string, unknown>>)
      : null

  const resumeLine = [
    strategyInfo.primary,
    core.hit1 !== null ? `Hit@1=${formatRate(core.hit1, 1)}` : null,
    core.hit3 !== null ? `Hit@3=${formatRate(core.hit3, 1)}` : null,
    core.hit5 !== null ? `Hit@5=${formatRate(core.hit5, 1)}` : null,
    core.mrr !== null ? `MRR=${formatMrr(core.mrr)}` : null,
    core.cases !== null ? `N=${core.cases}` : null,
    strategyInfo.rerankEnabled ? 'rerank=local_lexical_fusion' : null,
  ]
    .filter(Boolean)
    .join(' · ')

  const resumeExamples =
    strategyComparison && Object.keys(strategyComparison).length > 0
      ? Object.entries(strategyComparison)
          .map(([strategy, metrics]) => {
            const hit5 =
              pickMetric(metrics, 'hit_at_5') ??
              pickMetric(metrics, 'hit_at_3') ??
              pickMetric(metrics, 'hit_at_1')
            const mrr = pickMetric(metrics, 'mrr')
            if (hit5 === null && mrr === null) return null
            return `${strategyLabel(strategy)} Hit@5=${formatRate(hit5, 1)}，MRR=${formatMrr(mrr)}`
          })
          .filter(Boolean)
          .join('；')
      : `${strategyInfo.primary} Hit@5=${formatRate(core.hit5, 1)}，MRR=${formatMrr(core.mrr)}（N=${core.cases ?? '—'}）`

  const perfectScore =
    core.hit1 === 1 &&
    (core.hit5 === 1 || core.hit3 === 1) &&
    core.mrr === 1

  async function handleExport(format: 'json' | 'csv') {
    try {
      setExporting(format)
      const { exportEvalRun } = await import('../../api/eval')
      const blob = await exportEvalRun(id, format)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `eval-run-${id}.${format}`
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (error) {
      console.error(error)
    } finally {
      setExporting(null)
    }
  }

  return (
    <Card
      size="small"
      style={{ marginTop: 16 }}
      title={`${run.name} · ${strategyInfo.primary} 检索量化结果`}
      extra={
        <Space>
          <Button
            size="small"
            autoInsertSpace={false}
            loading={exporting === 'json'}
            onClick={() => void handleExport('json')}
          >
            导出 JSON
          </Button>
          <Button
            size="small"
            autoInsertSpace={false}
            loading={exporting === 'csv'}
            onClick={() => void handleExport('csv')}
          >
            导出 CSV
          </Button>
          <Button size="small" autoInsertSpace={false} onClick={onClose}>
            关闭
          </Button>
        </Space>
      }
    >
      <Space wrap style={{ marginBottom: 8 }}>
        <Tag color={statusColor(run.status)}>{run.status}</Tag>
        <Tag color="blue">主策略：{strategyInfo.primary}</Tag>
        {strategyInfo.compare.map((item) => (
          <Tag key={item}>对比：{item}</Tag>
        ))}
        {strategyInfo.rerankEnabled ? <Tag>本地重排</Tag> : null}
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          Request ID：{run.requestId ?? '无'}
        </Typography.Text>
      </Space>

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        title="简历可直接写的主指标（含检索方式）"
        description={
          <>
            <div>
              {resumeLine ||
                '等待 retrieval Run 完成后显示 Hit@K / MRR。请确保选择了检索用例并启动 retrieval 评估。'}
            </div>
            {resumeLine ? (
              <div style={{ marginTop: 6 }}>
                示例写法：{resumeExamples || strategyInfo.summary}
              </div>
            ) : null}
          </>
        }
      />

      {perfectScore ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          title="满分结果可信度警告"
          description="Hit@K/MRR 全是 100% 通常意味着语料太小或干扰文档不足（1 文档金标几乎必然排第一）。请重新导入：评测问 50 + 干扰文档 ≥200，并等索引完成后再跑。不要把这种满分直接写进简历。"
        />
      ) : null}

      <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="Hit@1" value={formatRate(core.hit1)} valueStyle={{ color: '#1677ff' }} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="Hit@3" value={formatRate(core.hit3)} valueStyle={{ color: '#1677ff' }} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="Hit@5" value={formatRate(core.hit5)} valueStyle={{ color: '#1677ff' }} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="MRR" value={formatMrr(core.mrr)} valueStyle={{ color: '#cf1322' }} />
          </Card>
        </Col>
      </Row>

      {core.hit10 !== null ? (
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          补充：Hit@10 = {formatRate(core.hit10)}
          {core.cases !== null ? ` · 完成用例 ${core.cases}` : ''}
        </Typography.Paragraph>
      ) : null}

      {strategyComparison ? (
        <Card size="small" type="inner" title="策略对比（只看 Hit@K / MRR）" style={{ marginBottom: 12 }}>
          <Table
            size="small"
            pagination={false}
            rowKey="strategy"
            dataSource={Object.entries(strategyComparison).map(([strategy, metrics]) => ({
              strategy: strategyLabel(strategy),
              hit1: metrics.hit_at_1,
              hit3: metrics.hit_at_3,
              hit5: metrics.hit_at_5 ?? metrics.hit_at_3,
              mrr: metrics.mrr,
            }))}
            columns={[
              { title: '策略', dataIndex: 'strategy' },
              {
                title: 'Hit@1',
                dataIndex: 'hit1',
                render: (value) => formatRate(value),
              },
              {
                title: 'Hit@3',
                dataIndex: 'hit3',
                render: (value) => formatRate(value),
              },
              {
                title: 'Hit@5',
                dataIndex: 'hit5',
                render: (value) => formatRate(value),
              },
              {
                title: 'MRR',
                dataIndex: 'mrr',
                render: (value) => formatMrr(value),
              },
            ]}
          />
        </Card>
      ) : null}

      {run.errorSummary ? (
        <Alert type="error" showIcon style={{ marginBottom: 12 }} title={run.errorSummary} />
      ) : null}

      <Collapse
        items={[
          {
            key: 'config',
            label: '配置快照 / 完整 metrics（调试用）',
            children: (
              <pre style={{ margin: 0, overflow: 'auto', fontSize: 12 }}>
                {JSON.stringify(
                  {
                    metrics: run.metrics,
                    configSnapshot: run.configSnapshot,
                  },
                  null,
                  2,
                )}
              </pre>
            ),
          },
        ]}
      />

      <Collapse
        style={{ marginTop: 16 }}
        items={[
          {
            key: 'cases',
            label: `展开逐条检索结果（${run.results.length} 条，99% 情况不用看）`,
            children:
              run.results.length === 0 ? (
                <Empty description="暂无结果；Run 完成后刷新" />
              ) : (
                <Space orientation="vertical" size={12} style={{ width: '100%' }}>
                  {run.results.slice(0, 100).map((result) => (
                    <RetrievalResultCard key={result.id} result={result} />
                  ))}
                  {run.results.length > 100 ? (
                    <Typography.Text type="secondary">
                      仅展示前 100 条，完整结果请导出 JSON/CSV。
                    </Typography.Text>
                  ) : null}
                </Space>
              ),
          },
        ]}
      />
    </Card>
  )
}

function RetrievalResultCard({ result }: { result: EvalResult }) {
  const metrics = result.retrievalMetrics
  const mrr = pickMetric(metrics, 'mrr')
  const hit1 = pickMetric(metrics, 'hit_at_1')
  const hit5 = pickMetric(metrics, 'hit_at_5') ?? pickMetric(metrics, 'hit_at_3')
  const firstRank = pickMetric(metrics, 'first_relevant_rank')
  const status = metrics?.status

  return (
    <Card size="small" type="inner">
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <Typography.Text strong style={{ flex: 1 }}>
          {result.question}
        </Typography.Text>
        <Space wrap>
          <Tag color={result.passed ? 'success' : status === 'skipped' ? 'default' : 'error'}>
            {result.passed ? '命中' : status === 'skipped' ? '跳过' : '未命中'}
          </Tag>
          <Tag>Hit@1 {formatRate(hit1, 0)}</Tag>
          <Tag>Hit@5 {formatRate(hit5, 0)}</Tag>
          <Tag color="red">MRR {formatMrr(mrr)}</Tag>
          {firstRank !== null ? <Tag>首个相关位次 #{firstRank}</Tag> : null}
        </Space>
      </div>
      {result.errorMessage ? (
        <Typography.Text type="danger" style={{ display: 'block', marginTop: 8 }}>
          {result.errorMessage}
        </Typography.Text>
      ) : null}
      {result.answer ? (
        <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }} ellipsis={{ rows: 2 }}>
          {result.answer}
        </Typography.Paragraph>
      ) : null}
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
