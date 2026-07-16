import { PlusOutlined } from '@ant-design/icons'
import {
  Button,
  Card,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { LoadingState } from '../../components/feedback/state-views'
import type { ColumnsType } from 'antd/es/table'
import { useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import type { Draft, DraftType } from '../../api/drafts'
import { useCreateDraftMutation, useDraftsQuery } from './queries'

const draftTypes: Array<{ value: DraftType; label: string }> = [
  { value: 'email', label: '邮件' },
  { value: 'checklist', label: '清单' },
  { value: 'application', label: '申请' },
  { value: 'faq', label: 'FAQ' },
  { value: 'help_request', label: '求助' },
  { value: 'summary', label: '摘要' },
]

const statusMap: Record<string, { label: string; color: string }> = {
  draft: { label: '草稿', color: 'default' },
  confirmed: { label: '已确认', color: 'success' },
  discarded: { label: '已丢弃', color: 'error' },
  exported: { label: '已导出', color: 'processing' },
}

const typeLabel = Object.fromEntries(draftTypes.map((item) => [item.value, item.label])) as Record<
  string,
  string
>

function positiveInt(value: string | null, fallback: number): number {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback
}

export function DraftListPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [createOpen, setCreateOpen] = useState(false)
  const page = positiveInt(searchParams.get('page'), 1)
  const pageSize = Math.min(positiveInt(searchParams.get('page_size'), 20), 100)
  const status = searchParams.get('status') ?? ''
  const draftType = searchParams.get('draft_type') ?? ''
  const query = useDraftsQuery(page, pageSize, status, draftType)

  function updateParams(patch: Record<string, string | null>) {
    const next = new URLSearchParams(searchParams)
    Object.entries(patch).forEach(([key, value]) => {
      if (!value) next.delete(key)
      else next.set(key, value)
    })
    setSearchParams(next, { replace: true })
  }

  const columns: ColumnsType<Draft> = useMemo(
    () => [
      {
        title: '标题',
        dataIndex: 'title',
        key: 'title',
        render: (title: string, record) => (
          <Link to={`/drafts/${record.id}`} style={{ fontWeight: 600 }}>
            {title}
          </Link>
        ),
      },
      {
        title: '类型',
        dataIndex: 'draftType',
        width: 120,
        render: (value: string) => typeLabel[value] ?? value,
      },
      {
        title: '状态',
        dataIndex: 'status',
        width: 120,
        render: (value: string) => {
          const meta = statusMap[value] ?? { label: value, color: 'default' }
          return <Tag color={meta.color}>{meta.label}</Tag>
        },
      },
      {
        title: '摘要',
        dataIndex: 'content',
        ellipsis: true,
        render: (value: string) => (
          <Typography.Text type="secondary">{value}</Typography.Text>
        ),
      },
      {
        title: '操作',
        key: 'actions',
        width: 100,
        render: (_, record) => (
          <Button type="link" size="small">
            <Link to={`/drafts/${record.id}`}>查看</Link>
          </Button>
        ),
      },
    ],
    [],
  )

  return (
      <div>
        <div className="page-header">
          <div>
            <h2>我的草稿</h2>
            <p>草稿仅在确认后变为只读，不会自动提交到外部系统。</p>
          </div>
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            <PlusOutlined aria-hidden />
            创建草稿
          </Button>
        </div>

        <Card styles={{ body: { paddingBottom: 8 } }}>
          <div className="page-toolbar">
            <Space wrap>
              <Select
                aria-label="状态"
                allowClear
                placeholder="全部状态"
                style={{ width: 140 }}
                value={status || undefined}
                onChange={(value) => updateParams({ status: value || null, page: '1' })}
                options={[
                  { value: 'draft', label: '草稿' },
                  { value: 'confirmed', label: '已确认' },
                  { value: 'discarded', label: '已丢弃' },
                  { value: 'exported', label: '已导出' },
                ]}
              />
              <Select
                aria-label="类型"
                allowClear
                placeholder="全部类型"
                style={{ width: 140 }}
                value={draftType || undefined}
                onChange={(value) => updateParams({ draft_type: value || null, page: '1' })}
                options={draftTypes}
              />
            </Space>
          </div>

          {query.isPending ? (
            <LoadingState message="正在加载草稿…" minH="min-h-48" />
          ) : (
            <Table
              rowKey="id"
              columns={columns}
              dataSource={query.data?.items ?? []}
              locale={{
                emptyText: (
                  <Empty
                    description={
                      query.isError
                        ? query.error.message
                        : '还没有符合条件的草稿'
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
                onChange: (nextPage) => updateParams({ page: String(nextPage) }),
              }}
            />
          )}
        </Card>

        <CreateDraftModal open={createOpen} onOpenChange={setCreateOpen} />
      </div>
  )
}

function CreateDraftModal({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const navigate = useNavigate()
  const mutation = useCreateDraftMutation()
  const [form] = Form.useForm()

  async function submit(values: {
    draftType: DraftType
    title: string
    content: string
    sourceQuestion?: string
  }) {
    try {
      const created = await mutation.mutateAsync({
        draftType: values.draftType,
        title: values.title.trim(),
        content: values.content.trim(),
        sourceQuestion: values.sourceQuestion?.trim() ?? '',
      })
      form.resetFields()
      onOpenChange(false)
      navigate(`/drafts/${created.id}`)
    } catch {
      // keep modal open so the user can retry
    }
  }

  return (
    <Modal
      title="创建草稿"
      open={open}
      onCancel={() => {
        if (!mutation.isPending) {
          form.resetFields()
          onOpenChange(false)
        }
      }}
      onOk={() => void form.submit()}
      confirmLoading={mutation.isPending}
      okText="创建草稿"
      destroyOnHidden
      width={640}
    >
      <Form
        form={form}
        layout="vertical"
        requiredMark={false}
        initialValues={{ draftType: 'email' }}
        onFinish={submit}
        style={{ marginTop: 16 }}
      >
        <Form.Item label="类型" name="draftType" rules={[{ required: true }]}>
          <Select options={draftTypes.map((item) => ({ value: item.value, label: item.label }))} />
        </Form.Item>
        <Form.Item
          label="标题"
          name="title"
          rules={[{ required: true, message: '请输入标题' }, { max: 255 }]}
        >
          <Input />
        </Form.Item>
        <Form.Item
          label="正文"
          name="content"
          rules={[{ required: true, message: '请输入正文' }]}
        >
          <Input.TextArea rows={7} />
        </Form.Item>
        <Form.Item label="来源问题" name="sourceQuestion">
          <Input placeholder="可选" />
        </Form.Item>
      </Form>
    </Modal>
  )
}
