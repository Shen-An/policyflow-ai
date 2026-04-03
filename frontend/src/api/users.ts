import type { RoleCode } from './auth'
import { apiClient } from './client'

export type UserRecord = {
  id: string
  username: string
  email: string
  displayName: string
  department: { id: string; name: string } | null
  roles: RoleCode[]
  status: string
  createdAt: string
  updatedAt: string
}

export type UserListResult = { items: UserRecord[]; total: number; page: number; pageSize: number }
export type UserListParams = { page: number; pageSize: number; keyword?: string }
export type CreateUserInput = {
  username: string
  email: string
  displayName: string
  password: string
  departmentId?: string
  roleCodes: RoleCode[]
}

type UserRaw = {
  id: string
  username: string
  email: string
  display_name: string
  department: { id: string; name: string } | null
  roles: string[]
  status: string
  created_at: string
  updated_at: string
}
type UserListRaw = { items: UserRaw[]; total: number; page: number; page_size: number }

function toRoles(values: string[]): RoleCode[] {
  return values.filter((value): value is RoleCode => value === 'employee' || value === 'kb_admin' || value === 'sys_admin')
}

function toUser(raw: UserRaw): UserRecord {
  return {
    id: raw.id,
    username: raw.username,
    email: raw.email,
    displayName: raw.display_name,
    department: raw.department,
    roles: toRoles(raw.roles),
    status: raw.status,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
  }
}

export async function listUsers(params: UserListParams, signal?: AbortSignal): Promise<UserListResult> {
  const search = new URLSearchParams({ page: String(params.page), page_size: String(params.pageSize) })
  if (params.keyword) search.set('keyword', params.keyword)
  const raw = await apiClient.request<UserListRaw>(`/api/users?${search.toString()}`, { signal })
  return { items: raw.items.map(toUser), total: raw.total, page: raw.page, pageSize: raw.page_size }
}

export async function createUser(input: CreateUserInput): Promise<UserRecord> {
  const raw = await apiClient.request<UserRaw>('/api/users', {
    method: 'POST',
    body: JSON.stringify({
      username: input.username,
      email: input.email,
      display_name: input.displayName,
      password: input.password,
      department_id: input.departmentId || null,
      role_codes: input.roleCodes,
    }),
  })
  return toUser(raw)
}

export async function updateUserRoles(userId: string, roleCodes: RoleCode[]): Promise<UserRecord> {
  const raw = await apiClient.request<UserRaw>(`/api/users/${encodeURIComponent(userId)}/roles`, {
    method: 'PUT',
    body: JSON.stringify({ role_codes: roleCodes }),
  })
  return toUser(raw)
}
