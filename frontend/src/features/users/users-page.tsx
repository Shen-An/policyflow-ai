import { PlusOutlined, SearchOutlined } from '@ant-design/icons'
import { Button, Card, Input, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { UserRecord } from '../../api/users'
import { AppError } from '../../api/errors'
import { ErrorState } from '../../components/feedback/state-views'
import { CreateUserDialog } from './components/create-user-dialog'
import { EditRolesDialog } from './components/edit-roles-dialog'
import { useUsersQuery } from './queries'

function positiveInt(value: string | null, fallback: number): number {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

export function UsersPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const page = positiveInt(searchParams.get('page'), 1)
  const pageSize = Math.min(positiveInt(searchParams.get('page_size'), 20), 100)
  const keyword = searchParams.get('keyword')?.trim() ?? ''
  const [keywordInput, setKeywordInput] = useState(keyword)
  const [createOpen, setCreateOpen] = useState(false)
  const [editingUser, setEditingUser] = useState<UserRecord | null>(null)
  const params = useMemo(
    () => ({ page, pageSize, keyword: keyword || undefined }),
    [keyword, page, pageSize],
  )
  const query = useUsersQuery(params)

  useEffect(() => {
    if (keywordInput.trim() === keyword) return
    const timer = window.setTimeout(() => {
      const next = new URLSearchParams(searchParams)
      const normalized = keywordInput.trim()
      if (normalized) next.set('keyword', normalized)
      else next.delete('keyword')
      next.set('page', '1')
      setSearchParams(next, { replace: true })
    }, 300)
    return () => window.clearTimeout(timer)
  }, [keyword, keywordInput, searchParams, setSearchParams])

  const columns: ColumnsType<UserRecord> = [
    {
      title: '显示名 / 用户名',
      key: 'name',
      render: (_, user) => (
        <div>
          <div style={{ fontWeight: 600 }}>{user.displayName}</div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {user.username}
          </Typography.Text>
        </div>
      ),
    },
    { title: '邮箱', dataIndex: 'email' },
    {
      title: '部门',
      dataIndex: ['department', 'name'],
      render: (value?: string) => value ?? '未分配',
    },
    {
      title: '角色',
      dataIndex: 'roles',
      render: (roles: string[]) => (
        <Space size={[4, 4]} wrap>
          {roles.map((role) => (
            <Tag key={role}>{role}</Tag>
          ))}
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (status: string) => (
        <Tag color={status === 'active' ? 'success' : 'default'}>{status}</Tag>
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      width: 180,
      render: (value: string) => formatDate(value),
    },
    {
      title: '操作',
      key: 'actions',
      width: 120,
      render: (_, user) => (
        <Button size="small" onClick={() => setEditingUser(user)}>
          修改角色
        </Button>
      ),
    },
  ]

  return (
    <div>
      <div className="page-header">
        <div>
          <h2>用户管理</h2>
          <p>查看组织用户、创建账户并维护角色。删除、禁用和密码重置不在当前范围。</p>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
          创建用户
        </Button>
      </div>

      <Card>
        <div className="page-toolbar" style={{ justifyContent: 'space-between' }}>
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="搜索用户名、邮箱或显示名"
            value={keywordInput}
            onChange={(event) => setKeywordInput(event.target.value)}
            style={{ width: 320, maxWidth: '100%' }}
            aria-label="搜索用户"
          />
          <Typography.Text type="secondary">
            共 {query.data?.total ?? 0} 位用户
            {query.isFetching && !query.isPending ? '，正在刷新…' : ''}
          </Typography.Text>
        </div>

        {query.isError ? (
          <ErrorState
            error={query.error}
            onRetry={() => void query.refetch()}
            title="用户列表加载失败"
            requestId={query.error instanceof AppError ? query.error.requestId : undefined}
          />
        ) : (
          <Table
            rowKey="id"
            loading={query.isPending}
            columns={columns}
            dataSource={query.data?.items ?? []}
            locale={{
              emptyText: keyword ? '没有匹配的用户' : '还没有用户',
            }}
            pagination={{
              current: page,
              pageSize,
              total: query.data?.total ?? 0,
              showSizeChanger: false,
              showTotal: (total) => `共 ${total} 位`,
              onChange: (nextPage) => {
                const next = new URLSearchParams(searchParams)
                next.set('page', String(nextPage))
                setSearchParams(next)
              },
            }}
          />
        )}
      </Card>

      <CreateUserDialog open={createOpen} onOpenChange={setCreateOpen} />
      {editingUser ? (
        <EditRolesDialog
          key={editingUser.id}
          user={editingUser}
          open
          onOpenChange={(open) => {
            if (!open) setEditingUser(null)
          }}
        />
      ) : null}
    </div>
  )
}
