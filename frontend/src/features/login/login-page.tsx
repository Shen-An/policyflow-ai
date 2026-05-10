import { LockOutlined, UserOutlined } from '@ant-design/icons'
import { Alert, Button, Card, ConfigProvider, Form, Input, Typography } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { login } from '../../api/auth'
import { AppError } from '../../api/errors'
import { authStore } from '../../auth/auth-store'
import type { LoginFormValues } from './login-schema'

function errorMessage(error: unknown): string {
  if (error instanceof AppError && error.code === 'AUTH_INVALID_CREDENTIALS') {
    return '用户名或密码不正确，请检查后重新输入。'
  }
  if (error instanceof AppError) return error.message
  return '登录未能完成，请稍后重试。'
}

export function LoginPage() {
  const [summary, setSummary] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const summaryRef = useRef<HTMLDivElement>(null)
  const submittingRef = useRef(false)

  useEffect(() => {
    if (summary) summaryRef.current?.focus()
  }, [summary])

  async function onFinish(values: LoginFormValues) {
    if (submittingRef.current) return
    submittingRef.current = true
    setSummary(null)
    setSubmitting(true)
    try {
      const result = await login(values)
      authStore.authenticateForDuration(result.accessToken, result.expiresIn, result.user)
    } catch (error) {
      setSummary(errorMessage(error))
    } finally {
      submittingRef.current = false
      setSubmitting(false)
    }
  }

  return (
    <ConfigProvider
      autoInsertSpaceInButton={false}
      theme={{ token: { motion: false, colorPrimary: '#4f46e5' } }}
    >
      <div
        style={{
          minHeight: '100vh',
          display: 'grid',
          placeItems: 'center',
          padding: 24,
          background:
            'radial-gradient(circle at top left, rgba(79,70,229,0.12), transparent 40%), radial-gradient(circle at bottom right, rgba(59,130,246,0.1), transparent 35%), #f5f7fb',
        }}
      >
        <div style={{ width: '100%', maxWidth: 420 }}>
          <div style={{ textAlign: 'center', marginBottom: 24 }}>
            <div
              style={{
                width: 52,
                height: 52,
                margin: '0 auto',
                borderRadius: 14,
                background: '#4f46e5',
                color: '#fff',
                display: 'grid',
                placeItems: 'center',
                fontWeight: 700,
                fontSize: 22,
                boxShadow: '0 10px 30px rgba(79,70,229,0.28)',
              }}
            >
              P
            </div>
            <Typography.Title level={3} style={{ marginTop: 16, marginBottom: 4 }}>
              PolicyFlow AI
            </Typography.Title>
            <Typography.Text type="secondary">企业内部政策问答与流程助手</Typography.Text>
          </div>

          <Card
            title="登录账户"
            styles={{ header: { borderBottom: '1px solid #f0f0f0' } }}
            style={{ boxShadow: '0 12px 40px rgba(15,23,41,0.08)' }}
          >
            {summary ? (
              <div ref={summaryRef} tabIndex={-1} style={{ marginBottom: 16, outline: 'none' }}>
                <Alert type="error" showIcon message={summary} />
              </div>
            ) : null}

            <Form
              layout="vertical"
              requiredMark={false}
              validateTrigger={['onSubmit', 'onChange']}
              onFinish={onFinish}
              initialValues={{ username: '', password: '' }}
            >
              <Form.Item
                label="用户名"
                name="username"
                rules={[
                  { required: true, message: '请输入用户名' },
                  { max: 64, message: '用户名过长' },
                ]}
              >
                <Input
                  size="large"
                  prefix={<UserOutlined />}
                  autoComplete="username"
                  placeholder="请输入用户名"
                />
              </Form.Item>

              <Form.Item
                label="密码"
                name="password"
                rules={[
                  { required: true, message: '请输入密码' },
                  { max: 128, message: '密码过长' },
                ]}
              >
                <Input.Password
                  size="large"
                  prefix={<LockOutlined />}
                  autoComplete="current-password"
                  placeholder="请输入密码"
                />
              </Form.Item>

              <Button type="primary" htmlType="submit" size="large" block loading={submitting}>
                登录
              </Button>
            </Form>
          </Card>

          <Typography.Paragraph type="secondary" style={{ textAlign: 'center', marginTop: 16 }}>
            登录后仅可访问你被授权的知识库与管理功能
          </Typography.Paragraph>
        </div>
      </div>
    </ConfigProvider>
  )
}
