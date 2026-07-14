import { Bug, Trash } from '@phosphor-icons/react'
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
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { EvalResult, EvalRunScope, EvalRunSummary } from '../../api/eval'
import { LoadingState } from '../../components/feedback/state-views'
import { confirmAction } from '../../lib/confirm'
import { formatDateTime } from '../../lib/datetime'
import { palette } from '../../styles/palette'
import { useKnowledgeBasesQuery } from '../knowledge-bases/queries'
import {
  useCleanupEvalDatasetMutation,
  useCreateEvalCaseMutation,
  useCreateEvalRunMutation,
  useCreateRetrievalItemMutation,
  useDeleteEvalRunMutation,
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

function statusLabel(value: string): string {
  if (value === 'success' || value === 'passed') return '成功'
  if (value === 'failed') return '失败'
  if (value === 'skipped') return '跳过'
  if (value === 'disabled') return '已禁用'
  if (value === 'running') return '运行中'
  if (value === 'pending') return '排队中'
  return value
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

function formatScopeLabel(scope: EvalRunScope | null | undefined): string | null {
  if (!scope) return null
  if (scope.label) return scope.label
  const parts: string[] = []
  if (scope.knowledgeBases.length) {
    parts.push(scope.knowledgeBases.map((item) => `${item.name}(${item.code})`).join('/'))
  }
  if (scope.taskTypes.length) parts.push(scope.taskTypes.join('+'))
  else if (scope.sources.length) parts.push(scope.sources.join('+'))
  if (scope.itemCount) parts.push(`N=${scope.itemCount}`)
  if (scope.staleGoldCount) parts.push(`stale_gold=${scope.staleGoldCount}`)
  return parts.length ? parts.join(' · ') : null
}

function rankHistogramText(metrics: Record<string, unknown> | null | undefined): string | null {
  const hist = metrics?.first_rank_histogram
  if (!hist || typeof hist !== 'object' || Array.isArray(hist)) return null
  const entries = Object.entries(hist as Record<string, unknown>)
    .map(([key, value]) => {
      const count = asNumber(value)
      return count === null ? null : { key, count }
    })
    .filter((item): item is { key: string; count: number } => item !== null)
    .sort((a, b) => {
      if (a.key === 'miss') return 1
      if (b.key === 'miss') return -1
      return Number(a.key) - Number(b.key)
    })
  if (!entries.length) return null
  return entries
    .map((item) => (item.key === 'miss' ? `未命中 ${item.count}` : `rank#${item.key} ${item.count}`))
    .join(' · ')
}

export function EvaluationPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const runId = searchParams.get('run_id') ?? ''

  return (
    <div>
      <p className="page-lede" style={{ marginBottom: 14 }}>
        两步完成检索量化：导入测试语料 → 随机抽样跑 Run，主看
        <strong> Hit@1 / Hit@5 / Hit@10 / MRR</strong>
        。
      </p>
      <Space orientation="vertical" size={16} style={{ width: '100%' }}>
        <CrudImportSection />
        <RunSection
          selectedRunId={runId}
          onSelectRun={(id) => {
            const next = new URLSearchParams(searchParams)
            if (id) next.set('run_id', id)
            else next.delete('run_id')
            setSearchParams(next)
          }} />
        <Collapse
          ghost
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
          ]} />
      </Space>
    </div>
  )
}

function CrudImportSection() {
  const knowledgeBases = useKnowledgeBasesQuery()
  const importMutation = useImportCrudDatasetMutation()
  const cleanupMutation = useCleanupEvalDatasetMutation()
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
    <Card title="1. 导入测试语料">
      <Typography.Paragraph type="secondary" style={{ marginTop: 0, marginBottom: 16 }}>
        导入到专用「测试库」(code=eval_test)，避免污染业务库。建议先 50 问 + 干扰文档 ≥200；索引在后台排队。
        跑 Run 前若 scope 混入业务库或出现 stale gold，先点「清理脏用例」。
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
        <Row gutter={[16, 0]}>
          <Col xs={24} sm={12} md={6}>
            <Form.Item label="评测问题数" name="sampleSize">
              <Input type="number" min={1} max={2000} />
            </Form.Item>
          </Col>
          <Col xs={24} sm={12} md={6}>
            <Form.Item label="干扰文档数" name="distractorCount">
              <Input type="number" min={0} max={5000} />
            </Form.Item>
          </Col>
          <Col xs={24} sm={12} md={6}>
            <Form.Item label="任务类型" name="taskType">
              <Select
                options={[
                  { value: 'questanswer_1doc', label: '1doc（主评测）' },
                  { value: 'questanswer_2docs', label: '2docs' },
                  { value: 'questanswer_3docs', label: '3docs' },
                ]} />
            </Form.Item>
          </Col>
          <Col xs={24} sm={12} md={6}>
            <Form.Item label="目标库" name="knowledgeBaseId">
              <Select
                allowClear
                placeholder={evalTestKb ? `默认 ${evalTestKb.name}` : '自动创建测试库'}
                options={(knowledgeBases.data ?? []).map((kb) => ({
                  value: kb.id,
                  label:
                    kb.code === 'eval_test' || kb.name === '测试库'
                      ? `${kb.name}（推荐）`
                      : `${kb.name}（${kb.code}）`,
                }))} />
            </Form.Item>
          </Col>
        </Row>

        <Collapse
          ghost
          style={{ marginBottom: 12 }}
          items={[
            {
              key: 'import-advanced',
              label: '高级导入选项',
              children: (
                <Row gutter={[16, 0]}>
                  <Col xs={24} md={12}>
                    <Form.Item
                      label="数据集路径（可选）"
                      name="sourcePath"
                      extra="默认使用服务端配置的 split_merged.json"
                    >
                      <Input placeholder="可填绝对路径；通常留空" />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={6}>
                    <Form.Item name="indexDocuments" valuePropName="checked">
                      <Checkbox>导入后后台建索引</Checkbox>
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={6}>
                    <Form.Item name="createEvalCases" valuePropName="checked">
                      <Checkbox>同时创建回答用例</Checkbox>
                    </Form.Item>
                  </Col>
                </Row>
              ),
            },
          ]} />

        {importMutation.isPending ? (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            title="正在导入…"
            description="写入测试库与评估用例；索引会在后台继续，不会卡住本页。" />
        ) : null}
        <Space wrap>
          <Button
            type="primary"
            htmlType="submit"
            autoInsertSpace={false}
            loading={importMutation.isPending}
          >
            {importMutation.isPending ? '导入中…' : '导入到测试库'}
          </Button>
          <Button
            autoInsertSpace={false}
            loading={cleanupMutation.isPending}
            onClick={() => {
              confirmAction({
                title: '清理脏评测用例？',
                content:
                  '将删除 stale gold（金标文档已删/空）检索用例、禁用非「测试库」用例，并物理清除软删除文档。清理后请重新「随机 50」再跑 Run。',
                okText: '清理',
                cancelText: '取消',
                onOk: async () => {
                  const result = await cleanupMutation.mutateAsync({})
                  message.success(
                    `已清理：stale ${result.staleItemsDeleted}，禁用非测试库 ${result.nonEvalItemsDisabled}，清除文档 ${result.deletedDocumentsPurged}；可用 eval_test ${result.evalTestEnabledItems} 条`,
                  )
                },
              })
            }}
          >
            清理脏用例
          </Button>
        </Space>
        {importMutation.isError ? (
          <Alert
            type="error"
            showIcon
            style={{ marginTop: 12 }}
            title={importMutation.error.message}
            description="超时可把问题数降到 20–30，或取消索引后先导入。" />
        ) : null}
        {importMutation.isSuccess ? (
          <Alert
            type={importMutation.data.warning ? 'warning' : 'success'}
            showIcon
            style={{ marginTop: 12 }}
            title={`导入完成：评测问 +${importMutation.data.retrievalItemsCreated}，文档 +${importMutation.data.documentsCreated}（干扰 ${importMutation.data.distractorDocumentsCreated}）${importMutation.data.indexQueued ? `，后台索引 ${importMutation.data.indexQueued}` : ''}`}
            description={
              importMutation.data.warning ||
              '索引完成后，在下方启动检索 Run 查看 Hit@K / MRR。'
            } />
        ) : null}
        {cleanupMutation.isError ? (
          <Alert
            type="error"
            showIcon
            style={{ marginTop: 12 }}
            title={cleanupMutation.error.message} />
        ) : null}
        {cleanupMutation.isSuccess ? (
          <Alert
            type={
              cleanupMutation.data.remainingStaleEnabledItems > 0
                ? 'warning'
                : 'success'
            }
            showIcon
            style={{ marginTop: 12 }}
            title={`清理完成：stale −${cleanupMutation.data.staleItemsDeleted}，禁用非测试库 ${cleanupMutation.data.nonEvalItemsDisabled}，回答用例禁用 ${cleanupMutation.data.evalCasesDisabled}，软删文档清除 ${cleanupMutation.data.deletedDocumentsPurged}`}
            description={`剩余 enabled ${cleanupMutation.data.remainingEnabledItems}（其中 stale ${cleanupMutation.data.remainingStaleEnabledItems}）；健康 eval_test ${cleanupMutation.data.evalTestEnabledItems} 条，可直接随机抽样。`} />
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
                  title={createCase.error.message} />
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
                  }))} />
              </Form.Item>
              <Form.Item label="关联回答用例" name="evalCaseId">
                <Select
                  allowClear
                  placeholder="无"
                  options={(cases.data ?? []).map((item) => ({
                    value: item.id,
                    label: item.question,
                  }))} />
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
                  title={createItem.error.message} />
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
                  <Card type="inner" size="small" title="回答用例">
                    {(cases.data ?? []).slice(0, 50).length === 0 ? (
                      <Empty description="暂无用例" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                    ) : (
                      <Space orientation="vertical" size={8} style={{ width: '100%' }}>
                        {(cases.data ?? []).slice(0, 50).map((item) => (
                          <Typography.Text key={item.id}>
                            {item.enabled ? 'enabled' : 'disabled'} · {item.category} · {item.question}
                          </Typography.Text>
                        ))}
                      </Space>
                    )}
                  </Card>
                </Col>
                <Col xs={24} lg={12}>
                  <Card type="inner" size="small" title="检索用例">
                    {(items.data ?? []).slice(0, 50).length === 0 ? (
                      <Empty description="暂无用例" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                    ) : (
                      <Space orientation="vertical" size={8} style={{ width: '100%' }}>
                        {(items.data ?? []).slice(0, 50).map((item) => (
                          <Typography.Text key={item.id}>
                            {item.enabled ? 'enabled' : 'disabled'} · {item.query}
                          </Typography.Text>
                        ))}
                      </Space>
                    )}
                  </Card>
                </Col>
              </Row>
            ),
          },
        ]} />
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
  const deleteRun = useDeleteEvalRunMutation()
  const [form] = Form.useForm()
  const [customSampleSize, setCustomSampleSize] = useState('50')
  const selectedItemCount =
    ((Form.useWatch('itemIds', form) as string[] | undefined) ?? []).length

  const columns: ColumnsType<EvalRunSummary> = useMemo(
    () => [
      {
        title: '名称',
        dataIndex: 'name',
        render: (value: string, run) => {
          const scopeLabel = formatScopeLabel(run.scope)
          return (
            <div>
              <Typography.Text strong>{value}</Typography.Text>
              {scopeLabel ? (
                <div>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {scopeLabel}
                  </Typography.Text>
                </div>
              ) : null}
            </div>
          )
        },
      },
      {
        title: '状态',
        dataIndex: 'status',
        width: 100,
        render: (value: string) => <Tag color={statusColor(value)}>{statusLabel(value)}</Tag>,
      },
      {
        title: 'Hit@1',
        key: 'hit1',
        width: 80,
        render: (_, run) => formatRate(coreRetrievalMetrics(run.metrics).hit1),
      },
      {
        title: 'Hit@5',
        key: 'hit5',
        width: 80,
        render: (_, run) => formatRate(coreRetrievalMetrics(run.metrics).hit5),
      },
      {
        title: 'Hit@10',
        key: 'hit10',
        width: 80,
        render: (_, run) => formatRate(coreRetrievalMetrics(run.metrics).hit10),
      },
      {
        title: 'MRR',
        key: 'mrr',
        width: 90,
        render: (_, run) => formatMrr(coreRetrievalMetrics(run.metrics).mrr),
      },
      { title: '用例', dataIndex: 'totalCases', width: 70 },
      {
        title: '创建时间',
        dataIndex: 'createdAt',
        width: 170,
        render: (value: string) => formatDateTime(value, undefined, value),
      },
      {
        title: '操作',
        key: 'actions',
        width: 180,
        render: (_, run) => (
          <Space size={4}>
            <Button size="small" autoInsertSpace={false} onClick={() => onSelectRun(run.id)}>
              查看分数
            </Button>
            <Button
              size="small"
              danger
              autoInsertSpace={false}
              loading={deleteRun.isPending}
              icon={<Trash size={16} weight="duotone" />}
              onClick={() => {
                confirmAction({
                  title: `物理删除 Run「${run.name}」？`,
                  content: '将永久删除该 Run 及其逐条结果，不可恢复。',
                  okText: '永久删除',
                  okButtonProps: { danger: true },
                  cancelText: '取消',
                  onOk: async () => {
                    await deleteRun.mutateAsync(run.id)
                    if (selectedRunId === run.id) onSelectRun('')
                    message.success('评估 Run 已物理删除')
                  },
                })
              }}
            >
              删除
            </Button>
          </Space>
        ),
      },
    ],
    [deleteRun, onSelectRun, selectedRunId],
  )

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Card title="2. 启动检索评估">
        <Typography.Paragraph type="secondary" style={{ marginTop: 0, marginBottom: 16 }}>
          默认只跑检索 Hit@K / MRR。列表仅含 enabled 用例（清理后应为纯测试库）。点「随机 50」后启动即可；策略对比与回答评估放在高级选项。
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

            if (!evalTypes.length) {
              throw new Error('请至少勾选一种评估类型（检索评估请勾「检索」）。')
            }
            if (evalTypes.includes('retrieval') && itemIds.length === 0) {
              throw new Error(
                '检索评估必须勾选至少一个「检索用例」。请先导入 CRUD 到测试库，再点「随机 50」。',
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
          <Row gutter={[16, 0]}>
            <Col xs={24} md={10}>
              <Form.Item
                label="Run 名称"
                name="name"
                rules={[{ required: true, message: '请输入名称' }]}
              >
                <Input placeholder="例如：Hybrid-N50" />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="主策略" name="strategy">
                <Select
                  options={[
                    { value: 'hybrid_lightrag_bm25', label: 'Hybrid (LightRAG+BM25)' },
                    { value: 'lightrag_only', label: 'LightRAG only' },
                    { value: 'bm25_only', label: 'BM25 only' },
                  ]} />
              </Form.Item>
            </Col>
            <Col xs={24} md={6}>
              <Form.Item label="抽样" style={{ marginBottom: 8 }}>
                <Space wrap>
                  <Button
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
                    disabled={!retrievalItems.data?.length}
                    onClick={() => {
                      const ids = (retrievalItems.data ?? []).map((item) => item.id)
                      form.setFieldValue('itemIds', pickRandomIds(ids, 100))
                    }}
                  >
                    随机 100
                  </Button>
                </Space>
              </Form.Item>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                已选 {selectedItemCount} / {retrievalItems.data?.length ?? 0} 条检索用例
              </Typography.Text>
            </Col>
          </Row>

          {/* Keep fields registered even when advanced panel is closed */}
          <div style={{ display: 'none' }} aria-hidden>
            <Form.Item name="itemIds">
              <Checkbox.Group options={[]} />
            </Form.Item>
            <Form.Item name="caseIds">
              <Checkbox.Group options={[]} />
            </Form.Item>
            <Form.Item name="evalTypes">
              <Checkbox.Group
                options={[
                  { value: 'retrieval', label: '检索' },
                  { value: 'rag_answer', label: '回答' },
                  { value: 'ragas', label: 'RAGAS' },
                ]} />
            </Form.Item>
            <Form.Item name="compareStrategies">
              <Checkbox.Group options={[]} />
            </Form.Item>
            <Form.Item name="rerankEnabled" valuePropName="checked">
              <Checkbox />
            </Form.Item>
          </div>

          <Collapse
            ghost
            style={{ marginBottom: 12 }}
            items={[
              {
                key: 'run-advanced',
                label: '高级选项（策略对比 / 回答评估 / 精选用例）',
                children: (
                  <Row gutter={[16, 8]}>
                    <Col xs={24}>
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
                          ]} />
                      </Form.Item>
                    </Col>
                    <Col xs={24}>
                      <Form.Item
                        label="多策略对比"
                        name="compareStrategies"
                        extra="会按每个策略重跑检索用例，耗时成倍增加"
                      >
                        <Checkbox.Group
                          options={[
                            { value: 'hybrid_lightrag_bm25', label: 'Hybrid' },
                            { value: 'lightrag_only', label: 'LightRAG' },
                            { value: 'bm25_only', label: 'BM25' },
                          ]} />
                      </Form.Item>
                    </Col>
                    <Col xs={24}>
                      <Form.Item name="rerankEnabled" valuePropName="checked">
                        <Checkbox>
                          启用本地重排（lexical fusion，非 cross-encoder）
                        </Checkbox>
                      </Form.Item>
                    </Col>
                    <Col xs={24} md={12}>
                      <Form.Item
                        label={`回答用例（${cases.data?.length ?? 0}）`}
                        name="caseIds"
                        extra="仅「回答 / RAGAS」需要"
                      >
                        <Checkbox.Group
                          style={{
                            display: 'flex',
                            flexDirection: 'column',
                            maxHeight: 120,
                            overflow: 'auto',
                            gap: 6,
                          }}
                          options={(cases.data ?? []).map((item) => ({
                            value: item.id,
                            label: item.question,
                          }))} />
                      </Form.Item>
                    </Col>
                    <Col xs={24} md={12}>
                      <Form.Item
                        label={`检索用例（${retrievalItems.data?.length ?? 0}）`}
                        name="itemIds"
                        extra="建议用上方随机按钮，不要全选几百条"
                      >
                        <Checkbox.Group
                          style={{
                            display: 'flex',
                            flexDirection: 'column',
                            maxHeight: 140,
                            overflow: 'auto',
                            gap: 6,
                          }}
                          options={(retrievalItems.data ?? []).map((item) => ({
                            value: item.id,
                            label: item.query,
                          }))} />
                      </Form.Item>
                      <Space wrap>
                        <Space.Compact>
                          <Input
                            size="small"
                            type="number"
                            min={1}
                            placeholder="N"
                            style={{ width: 72 }}
                            value={customSampleSize}
                            onChange={(event) => setCustomSampleSize(event.target.value)} />
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
                      </Space>
                    </Col>
                  </Row>
                ),
              },
            ]} />

          <Space wrap>
            <Button
              type="primary"
              htmlType="submit"
              autoInsertSpace={false}
              loading={create.isPending}
              disabled={selectedItemCount === 0 && !(cases.data?.length)}
            >
              启动评估
            </Button>
            {selectedItemCount === 0 ? (
              <Typography.Text type="secondary">
                请先点「随机 50」选择检索用例
              </Typography.Text>
            ) : (
              <Typography.Text type="secondary">
                将用 {selectedItemCount} 条检索用例启动
              </Typography.Text>
            )}
          </Space>
          {create.isError ? (
            <Alert
              type="error"
              showIcon
              style={{ marginTop: 12 }}
              title={create.error.message}
              description="若只做检索量化：评估类型仅勾「检索」，并至少随机选一批检索用例。" />
          ) : null}
        </Form>
      </Card>

      <Card
        title="评估历史"
        extra={
          <Typography.Text type="secondary">
            共 {runs.data?.total ?? runs.data?.items?.length ?? 0} 次
          </Typography.Text>
        }
        styles={{ body: { paddingTop: 8 } }}
      >
        <Table
          rowKey="id"
          loading={runs.isPending}
          columns={columns}
          dataSource={runs.data?.items ?? []}
          pagination={false}
          locale={{ emptyText: <Empty description="暂无评估 Run，先导入语料再启动" /> }} />
      </Card>

      {selectedRunId ? (
        <RunDetail id={selectedRunId} onClose={() => onSelectRun('')} />
      ) : null}
    </Space>
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
    core.hit5 !== null ? `Hit@5=${formatRate(core.hit5, 1)}` : null,
    core.hit10 !== null ? `Hit@10=${formatRate(core.hit10, 1)}` : null,
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
            const hit10 = pickMetric(metrics, 'hit_at_10')
            const mrr = pickMetric(metrics, 'mrr')
            if (hit5 === null && hit10 === null && mrr === null) return null
            const hit10Part =
              hit10 !== null ? `，Hit@10=${formatRate(hit10, 1)}` : ''
            return `${strategyLabel(strategy)} Hit@5=${formatRate(hit5, 1)}${hit10Part}，MRR=${formatMrr(mrr)}`
          })
          .filter(Boolean)
          .join('；')
      : `${strategyInfo.primary} Hit@5=${formatRate(core.hit5, 1)}，Hit@10=${formatRate(core.hit10, 1)}，MRR=${formatMrr(core.mrr)}（N=${core.cases ?? '—'}）`

  const perfectScore =
    core.hit1 === 1 &&
    (core.hit5 === 1 || core.hit3 === 1) &&
    core.mrr === 1
  const scopeLabel = formatScopeLabel(run.scope)
  const rankHistText = rankHistogramText(run.metrics)
  const midRankHits = asNumber(run.metrics.mid_rank_hits) ?? 0
  const collapsedHits =
    core.hit1 !== null &&
    core.hit5 !== null &&
    core.hit1 === core.hit5 &&
    (core.hit10 === null || core.hit1 === core.hit10) &&
    core.mrr !== null &&
    Math.abs(core.mrr - core.hit1) < 1e-9

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
        <Tag color={statusColor(run.status)}>{statusLabel(run.status)}</Tag>
        <Tag color="blue">主策略：{strategyInfo.primary}</Tag>
        {strategyInfo.compare.map((item) => (
          <Tag key={item}>对比：{item}</Tag>
        ))}
        {strategyInfo.rerankEnabled ? <Tag>本地重排</Tag> : null}
        {run.scope?.taskTypes.map((item) => (
          <Tag key={`task-${item}`} color="purple">
            {item}
          </Tag>
        ))}
        {run.scope?.sources.map((item) => (
          <Tag key={`src-${item}`}>来源：{item}</Tag>
        ))}
        {run.scope?.knowledgeBases.map((item) => (
          <Tag key={item.id} color="cyan">
            KB：{item.name}({item.code})
          </Tag>
        ))}
        {run.scope && run.scope.staleGoldCount > 0 ? (
          <Tag color="error">stale gold {run.scope.staleGoldCount}</Tag>
        ) : null}
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          Request ID：{run.requestId ?? '无'}
        </Typography.Text>
      </Space>

      {scopeLabel ? (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          title="用例来源（与 Run 名称无关）"
          description={
            <>
              <div>
                自由文本名称只是标签；真正评测范围是：
                <strong> {scopeLabel}</strong>
              </div>
              <div style={{ marginTop: 4, color: '#64748b' }}>
                名称像「离职手续」不代表测的是 HR 离职制度——请以 KB / task_type /
                N 为准。
              </div>
            </>
          } />
      ) : null}

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
            {scopeLabel ? (
              <div style={{ marginTop: 6, color: '#64748b' }}>范围：{scopeLabel}</div>
            ) : null}
          </>
        } />

      {perfectScore ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          title="满分结果可信度警告"
          description="Hit@K/MRR 全是 100% 通常意味着语料太小或干扰文档不足（1 文档金标几乎必然排第一）。请重新导入：评测问 50 + 干扰文档 ≥200，并等索引完成后再跑。不要把这种满分直接写进简历。" />
      ) : null}

      {collapsedHits ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          title="Hit@1 / Hit@5 / Hit@10 数值相同"
          description={
            <>
              <div>
                这通常不是公式错误，而是几乎所有命中都在
                <strong> 第 1 位</strong>，未命中则
                <strong> top-10 全丢</strong>
                （没有 rank 2–10 的中间命中，所以放大 K 也分不开）。
              </div>
              {rankHistText ? (
                <div style={{ marginTop: 6 }}>首相关位次分布：{rankHistText}</div>
              ) : (
                <div style={{ marginTop: 6 }}>
                  旧 Run 可能没有位次直方图；重新跑一次会写入 first_rank_histogram。
                </div>
              )}
              {run.scope && run.scope.staleGoldCount > 0 ? (
                <div style={{ marginTop: 6 }}>
                  当前选中用例有 <strong>{run.scope.staleGoldCount}</strong>{' '}
                  条金标文档已删除/缺失（id 对不上重导入副本）。请清理旧检索用例后重新导入
                  CRUD，并等索引完成再评。
                </div>
              ) : null}
              {midRankHits === 0 ? (
                <div style={{ marginTop: 6, color: '#64748b' }}>
                  mid_rank_hits=0（没有任何用例首命中落在 #2–#10）。
                </div>
              ) : null}
            </>
          } />
      ) : null}

      <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
        <Col xs={12} sm={8} md={6} lg={4}>
          <Card size="small">
            <Statistic title="Hit@1" value={formatRate(core.hit1)} valueStyle={{ color: palette.primary }} />
          </Card>
        </Col>
        <Col xs={12} sm={8} md={6} lg={4}>
          <Card size="small">
            <Statistic title="Hit@5" value={formatRate(core.hit5)} valueStyle={{ color: palette.primary }} />
          </Card>
        </Col>
        <Col xs={12} sm={8} md={6} lg={4}>
          <Card size="small">
            <Statistic
              title="Hit@10"
              value={formatRate(core.hit10)}
              valueStyle={{ color: palette.primary }} />
          </Card>
        </Col>
        <Col xs={12} sm={8} md={6} lg={4}>
          <Card size="small">
            <Statistic title="MRR" value={formatMrr(core.mrr)} valueStyle={{ color: '#cf1322' }} />
          </Card>
        </Col>
        <Col xs={12} sm={8} md={6} lg={4}>
          <Card size="small">
            <Statistic title="完成用例 N" value={core.cases ?? '—'} />
          </Card>
        </Col>
        {core.hit3 !== null ? (
          <Col xs={12} sm={8} md={6} lg={4}>
            <Card size="small">
              <Statistic
                title="Hit@3（补充）"
                value={formatRate(core.hit3)}
                valueStyle={{ color: '#64748b' }} />
            </Card>
          </Col>
        ) : null}
      </Row>

      {strategyComparison ? (
        <Card size="small" type="inner" title="策略对比（只看 Hit@K / MRR）" style={{ marginBottom: 12 }}>
          <Table
            size="small"
            pagination={false}
            rowKey="strategy"
            dataSource={Object.entries(strategyComparison).map(([strategy, metrics]) => ({
              strategy: strategyLabel(strategy),
              hit1: metrics.hit_at_1,
              hit5: metrics.hit_at_5 ?? metrics.hit_at_3,
              hit10: metrics.hit_at_10,
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
                title: 'Hit@5',
                dataIndex: 'hit5',
                render: (value) => formatRate(value),
              },
              {
                title: 'Hit@10',
                dataIndex: 'hit10',
                render: (value) => formatRate(value),
              },
              {
                title: 'MRR',
                dataIndex: 'mrr',
                render: (value) => formatMrr(value),
              },
            ]} />
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
        ]} />

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
        ]} />
    </Card>
  )
}

function RetrievalResultCard({ result }: { result: EvalResult }) {
  const metrics = result.retrievalMetrics
  const mrr = pickMetric(metrics, 'mrr')
  const hit1 = pickMetric(metrics, 'hit_at_1')
  const hit5 = pickMetric(metrics, 'hit_at_5') ?? pickMetric(metrics, 'hit_at_3')
  const hit10 = pickMetric(metrics, 'hit_at_10')
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
          {hit10 !== null ? <Tag>Hit@10 {formatRate(hit10, 0)}</Tag> : null}
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
          <Bug size={16} weight="duotone" />
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
            }))} />
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
          size="small" />
      ) : null}
    </Card>
  )
}
