import {
  Button,
  Card,
  Col,
  Descriptions,
  Form,
  Input,
  Modal,
  Row,
  Select,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import { useState } from 'react'
import { useNavigate, useOutletContext } from 'react-router-dom'
import type { KnowledgeBase, QueryMode } from '../../api/knowledge-bases'
import {
  permissionColor,
  permissionLabel,
  queryModeLabel,
  queryModeOptions,
  statusColor,
  statusLabel,
} from './labels'
import {
  useDeleteKnowledgeBaseMutation,
  useUpdateKnowledgeBaseMutation,
} from './queries'

function canAdmin(permission: KnowledgeBase['permission']): boolean {
  return permission === 'admin'
}

export function KnowledgeBaseOverviewPage() {
  const navigate = useNavigate()
  const { knowledgeBase } = useOutletContext<{ knowledgeBase: KnowledgeBase }>()
  const [editing, setEditing] = useState(false)
  const [form] = Form.useForm()
  const updateMutation = useUpdateKnowledgeBaseMutation(knowledgeBase.id)
  const deleteMutation = useDeleteKnowledgeBaseMutation()
  const admin = canAdmin(knowledgeBase.permission)
  const isEvalTest = knowledgeBase.code === 'eval_test'
  const statusText = statusLabel[knowledgeBase.status] ?? knowledgeBase.status

  function openEdit() {
    form.setFieldsValue({
      name: knowledgeBase.name,
      description: knowledgeBase.description,
      defaultQueryMode: knowledgeBase.defaultQueryMode,
      status: knowledgeBase.status === 'disabled' ? 'disabled' : 'active',
    })
    setEditing(true)
  }

  async function handleSave() {
    const values = await form.validateFields()
    await updateMutation.mutateAsync({
      name: values.name.trim(),
      description: values.description ?? '',
      defaultQueryMode: values.defaultQueryMode as QueryMode,
      status: values.status,
    })
    message.success('知识库已更新')
    setEditing(false)
  }

  function handleDelete() {
    Modal.confirm({
      title: `物理删除知识库「${knowledgeBase.name}」？`,
      content: isEvalTest
        ? '将永久删除测试库、其文档、索引任务与本地工作区，且不可恢复。'
        : '将永久删除该知识库、其下全部文档、权限与本地工作区，且不可恢复。',
      okText: '永久删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        await deleteMutation.mutateAsync(knowledgeBase.id)
        message.success('知识库已物理删除')
        navigate('/knowledge-bases')
      },
    })
  }

  return (
    <Row gutter={[16, 16]}>
      <Col span={24}>
        {isEvalTest ? (
          <Typography.Paragraph type="secondary" style={{ marginTop: 0, marginBottom: 12 }}>
            这是评估专用「测试库」。CRUD / Hit@K 导入默认进入此库；演示完可直接删除。
          </Typography.Paragraph>
        ) : null}

        <Card
          size="small"
          title="基本信息"
          extra={
            admin ? (
              <Space wrap>
                <Button size="small" onClick={openEdit}>
                  编辑
                </Button>
                <Button
                  size="small"
                  danger
                  onClick={handleDelete}
                  loading={deleteMutation.isPending}
                >
                  删除知识库
                </Button>
              </Space>
            ) : (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                当前为只读权限
              </Typography.Text>
            )
          }
        >
          <Descriptions column={{ xs: 1, sm: 2 }} bordered size="small">
            <Descriptions.Item label="描述" span={2}>
              {knowledgeBase.description || '暂无描述'}
            </Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={statusColor[knowledgeBase.status] ?? 'default'}>{statusText}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="资源权限">
              <Tag color={permissionColor[knowledgeBase.permission]}>
                {permissionLabel[knowledgeBase.permission]}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="文档数量">{knowledgeBase.documentCount}</Descriptions.Item>
            <Descriptions.Item label="默认检索模式">
              {queryModeLabel[knowledgeBase.defaultQueryMode] ?? knowledgeBase.defaultQueryMode}
            </Descriptions.Item>
            <Descriptions.Item label="编码">
              <Typography.Text code>{knowledgeBase.code}</Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="部门 ID">
              <Typography.Text type="secondary">{knowledgeBase.departmentId}</Typography.Text>
            </Descriptions.Item>
          </Descriptions>
        </Card>
      </Col>

      <Modal
        title="编辑知识库"
        open={editing}
        onCancel={() => setEditing(false)}
        onOk={() => void handleSave()}
        confirmLoading={updateMutation.isPending}
        destroyOnHidden
        okText="保存"
      >
        <Form form={form} layout="vertical" requiredMark={false}>
          <Form.Item
            label="名称"
            name="name"
            rules={[{ required: true, message: '请输入名称' }]}
          >
            <Input maxLength={100} />
          </Form.Item>
          <Form.Item label="描述" name="description">
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item label="默认检索模式" name="defaultQueryMode">
            <Select options={queryModeOptions} />
          </Form.Item>
          <Form.Item label="状态" name="status">
            <Select
              options={[
                { value: 'active', label: '启用' },
                { value: 'disabled', label: '停用' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>
    </Row>
  )
}
