import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  CloudServerOutlined,
  KeyOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Typography,
} from 'antd'
import { useState } from 'react'
import type { ModelCapability, ModelEndpointSettings } from '../../api/model-settings'
import { LoadingState } from '../../components/feedback/state-views'
import {
  useModelSettingsQuery,
  useProviderModelsMutation,
  useSaveModelSettingsMutation,
  useTestModelProviderMutation,
} from './queries'

type FormState = {
  name: string
  baseUrl: string
  authMode: 'bearer' | 'none'
  apiStyle: 'openai_chat_completions' | 'openai_responses' | 'openai_embeddings'
  apiKey: string
  clearApiKey: boolean
  model: string
  embeddingDimension: number
  embeddingInputType: 'none' | 'query' | 'passage'
  timeoutSeconds: number
  enabled: boolean
}

function initialForm(
  capability: ModelCapability,
  provider: ModelEndpointSettings | null,
): FormState {
  if (provider) {
    return {
      name: provider.name,
      baseUrl: provider.baseUrl,
      authMode: provider.authMode === 'none' ? 'none' : 'bearer',
      apiStyle: provider.apiStyle,
      apiKey: '',
      clearApiKey: false,
      model: provider.model,
      embeddingDimension: provider.embeddingDimension ?? 1536,
      embeddingInputType: provider.baseUrl.includes('integrate.api.nvidia.com')
        ? 'none'
        : provider.embeddingInputType ?? 'none',
      timeoutSeconds: provider.timeoutSeconds,
      enabled: provider.enabled,
    }
  }
  return {
    name: capability === 'chat' ? 'chat-provider' : 'embedding-provider',
    baseUrl: '',
    authMode: 'bearer',
    apiStyle: capability === 'chat' ? 'openai_chat_completions' : 'openai_embeddings',
    apiKey: '',
    clearApiKey: false,
    model: '',
    embeddingDimension: 1536,
    embeddingInputType: 'none',
    timeoutSeconds: 120,
    enabled: true,
  }
}

function TestResult({
  result,
}: {
  result?: { status: string; message: string; dimension: number | null }
}) {
  if (!result) return null
  const passed = result.status === 'passed'
  return (
    <Alert
      type={passed ? 'success' : 'error'}
      showIcon
      icon={passed ? <CheckCircleOutlined /> : <CloseCircleOutlined />}
      title={passed ? '连接成功' : '连接失败'}
      description={`${result.message}${result.dimension ? `（维度 ${result.dimension}）` : ''}`}
    />
  )
}

function ProviderForm({
  capability,
  title,
  description,
  provider,
}: {
  capability: ModelCapability
  title: string
  description: string
  provider: ModelEndpointSettings | null
}) {
  const [form] = Form.useForm<FormState>()
  const [authMode, setAuthMode] = useState<'bearer' | 'none'>(
    provider?.authMode === 'none' ? 'none' : 'bearer',
  )
  const [apiStyle, setApiStyle] = useState(initialForm(capability, provider).apiStyle)
  const save = useSaveModelSettingsMutation()
  const catalog = useProviderModelsMutation()
  const test = useTestModelProviderMutation()

  return (
    <Card title={title}>
      <Typography.Paragraph type="secondary">{description}</Typography.Paragraph>
      <Form
        form={form}
        layout="vertical"
        requiredMark={false}
        initialValues={initialForm(capability, provider)}
        onFinish={(values) => {
          save.mutate(
            {
              capability,
              input: {
                name: values.name,
                baseUrl: values.baseUrl,
                authMode: values.authMode,
                apiStyle: values.apiStyle,
                apiKey: values.apiKey || undefined,
                clearApiKey: values.clearApiKey,
                model: values.model,
                embeddingDimension:
                  capability === 'embedding' ? values.embeddingDimension : null,
                embeddingInputType:
                  capability === 'embedding' ? values.embeddingInputType : null,
                timeoutSeconds: values.timeoutSeconds,
                enabled: values.enabled,
              },
            },
            {
              onSuccess: () =>
                form.setFieldsValue({ apiKey: '', clearApiKey: false }),
            },
          )
        }}
      >
        {capability === 'chat' ? (
          <Form.Item label="接口协议" name="apiStyle">
            <Select
              onChange={(value) => setApiStyle(value)}
              options={[
                { value: 'openai_chat_completions', label: 'OpenAI Chat Completions' },
                { value: 'openai_responses', label: 'OpenAI Responses API' },
              ]}
            />
          </Form.Item>
        ) : (
          <Form.Item name="apiStyle" hidden>
            <Input />
          </Form.Item>
        )}

        <Row gutter={16}>
          <Col xs={24} md={12}>
            <Form.Item
              label="配置名称"
              name="name"
              rules={[{ required: true, message: '请输入配置名称' }]}
            >
              <Input />
            </Form.Item>
          </Col>
          <Col xs={24} md={12}>
            <Form.Item
              label="Base URL / 完整 Endpoint"
              name="baseUrl"
              rules={[{ required: true, message: '请输入 Base URL' }]}
            >
              <Input
                placeholder={
                  capability === 'chat' && apiStyle === 'openai_responses'
                    ? 'https://provider.example.com/v1/responses'
                    : 'https://provider.example.com/v1'
                }
              />
            </Form.Item>
          </Col>
          <Col xs={24} md={12}>
            <Form.Item label="鉴权方式" name="authMode">
              <Select
                onChange={(value) => setAuthMode(value)}
                options={[
                  { value: 'bearer', label: 'Bearer API Key' },
                  { value: 'none', label: '无鉴权（本地模型）' },
                ]}
              />
            </Form.Item>
          </Col>
          <Col xs={24} md={12}>
            <Form.Item
              label={
                <Space>
                  <KeyOutlined />
                  API Key
                </Space>
              }
              name="apiKey"
            >
              <Input.Password
                autoComplete="new-password"
                disabled={authMode === 'none'}
                placeholder={
                  provider?.apiKeyConfigured ? '已配置；留空保持不变' : '输入 API Key'
                }
              />
            </Form.Item>
          </Col>
        </Row>

        {provider?.apiKeyConfigured && authMode === 'bearer' ? (
          <Form.Item name="clearApiKey" valuePropName="checked">
            <Checkbox>清除已保存的 API Key</Checkbox>
          </Form.Item>
        ) : null}

        <Row gutter={16}>
          <Col xs={24} md={12}>
            <Form.Item
              label={capability === 'chat' ? 'Chat 模型' : 'Embedding 模型'}
              name="model"
              rules={[{ required: true, message: '请输入模型名称' }]}
            >
              <Input list={`models-${capability}`} />
            </Form.Item>
            <datalist id={`models-${capability}`}>
              {catalog.data?.map((model) => (
                <option key={model} value={model} />
              ))}
            </datalist>
          </Col>
          {capability === 'embedding' ? (
            <>
              <Col xs={24} md={12}>
                <Form.Item label="Embedding 维度" name="embeddingDimension">
                  <InputNumber min={1} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
              <Col xs={24} md={12}>
                <Form.Item label="输入类型" name="embeddingInputType">
                  <Select
                    options={[
                      { value: 'none', label: '不发送（标准 OpenAI）' },
                      { value: 'query', label: 'query（问题/搜索词）' },
                      { value: 'passage', label: 'passage（文档内容）' },
                    ]}
                  />
                </Form.Item>
              </Col>
            </>
          ) : null}
          <Col xs={24} md={12}>
            <Form.Item label="超时（秒）" name="timeoutSeconds">
              <InputNumber min={1} max={600} style={{ width: '100%' }} />
            </Form.Item>
          </Col>
        </Row>

        <Form.Item name="enabled" valuePropName="checked">
          <Checkbox>启用此配置</Checkbox>
        </Form.Item>

        <Space wrap style={{ borderTop: '1px solid #e3e8f0', paddingTop: 16, width: '100%' }}>
          <Button type="primary" htmlType="submit" autoInsertSpace={false} loading={save.isPending}>
            保存 {title}
          </Button>
          <Button
            autoInsertSpace={false}
            onClick={() => catalog.mutate(capability)}
            loading={catalog.isPending}
          >
            拉取模型
          </Button>
          <Button
            autoInsertSpace={false}
            onClick={() => test.mutate(capability)}
            loading={test.isPending}
          >
            测试连接
          </Button>
        </Space>

        <Space orientation="vertical" size={12} style={{ width: '100%', marginTop: 16 }}>
          {save.isSuccess ? <Alert type="success" showIcon title="已保存并立即生效。" /> : null}
          {save.isError ? (
            <Alert type="error" showIcon title={save.error.message} />
          ) : null}
          {catalog.isSuccess ? (
            <Alert type="info" showIcon title={`发现 ${catalog.data.length} 个模型。`} />
          ) : null}
          {catalog.isError ? (
            <Alert type="error" showIcon title={catalog.error.message} />
          ) : null}
          <TestResult result={test.data?.result} />
          {test.data?.requestId ? (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Request ID: {test.data.requestId}
            </Typography.Text>
          ) : null}
        </Space>
      </Form>
    </Card>
  )
}

export function ModelSettingsPage() {
  const query = useModelSettingsQuery()

  if (query.isPending) return <LoadingState message="正在加载模型设置…" />
  if (query.isError) {
    return (
      <Alert
        type="error"
        showIcon
        message="模型设置加载失败"
        description={query.error.message}
        action={
          <Button size="small" onClick={() => void query.refetch()}>
            重试
          </Button>
        }
      />
    )
  }

  return (
    <div>
      <div className="page-header">
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
          <div
            style={{
              width: 44,
              height: 44,
              borderRadius: 12,
              background: 'rgba(79, 70, 229, 0.08)',
              color: '#4f46e5',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 22,
            }}
          >
            <CloudServerOutlined />
          </div>
          <div>
            <h2>模型设置</h2>
            <p>Chat 与 Embedding 使用完全独立的服务配置，可以分别接入不同公司。</p>
          </div>
        </div>
      </div>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={12}>
          <ProviderForm
            key={query.data.chat?.updatedAt ?? 'new-chat'}
            capability="chat"
            title="Chat 服务"
            description="用于制度问答、摘要和 Agent 生成。"
            provider={query.data.chat}
          />
        </Col>
        <Col xs={24} xl={12}>
          <ProviderForm
            key={query.data.embedding?.updatedAt ?? 'new-embedding'}
            capability="embedding"
            title="Embedding 服务"
            description="用于向量化和 Embedding 连通性验证。"
            provider={query.data.embedding}
          />
        </Col>
      </Row>

      <Alert
        type="warning"
        showIcon
        style={{ marginTop: 16 }}
        title="若检索由独立 LightRAG 服务执行，LightRAG 服务端的 Embedding 配置也需要与这里保持一致。"
      />
    </div>
  )
}
