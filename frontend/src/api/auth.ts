import { apiClient } from './client'

export type RoleCode = 'employee' | 'kb_admin' | 'sys_admin'

export type AuthUser = {
  id: string
  username: string
  displayName: string
  roles: RoleCode[]
  email?: string
  department?: { id: string; name: string } | null
  status?: string
  createdAt?: string
  updatedAt?: string
}

type LoginResponseRaw = {
  access_token: string
  token_type: string
  expires_in: number
  user: {
    id: string
    username: string
    display_name: string
    roles: string[]
  }
}

type CurrentUserRaw = {
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

export type LoginInput = { username: string; password: string }
export type LoginResult = { accessToken: string; expiresIn: number; user: AuthUser }

function toRoleCodes(roles: string[]): RoleCode[] {
  return roles.filter((role): role is RoleCode =>
    role === 'employee' || role === 'kb_admin' || role === 'sys_admin',
  )
}

function toLoginUser(raw: LoginResponseRaw['user']): AuthUser {
  return {
    id: raw.id,
    username: raw.username,
    displayName: raw.display_name,
    roles: toRoleCodes(raw.roles),
  }
}

function toCurrentUser(raw: CurrentUserRaw): AuthUser {
  return {
    id: raw.id,
    username: raw.username,
    email: raw.email,
    displayName: raw.display_name,
    department: raw.department,
    roles: toRoleCodes(raw.roles),
    status: raw.status,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
  }
}

export async function login(input: LoginInput, signal?: AbortSignal): Promise<LoginResult> {
  const response = await apiClient.request<LoginResponseRaw>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify(input),
    signal,
    skipUnauthorizedHandler: true,
  })
  return {
    accessToken: response.access_token,
    expiresIn: response.expires_in,
    user: toLoginUser(response.user),
  }
}

export async function getCurrentUser(signal?: AbortSignal): Promise<AuthUser> {
  const response = await apiClient.request<CurrentUserRaw>('/api/auth/me', { signal })
  return toCurrentUser(response)
}
