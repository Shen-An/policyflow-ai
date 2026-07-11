import type { RoleCode } from '../../api/auth'

export const roleOptions: Array<{ value: RoleCode; label: string }> = [
  { value: 'employee', label: '普通员工' },
  { value: 'kb_admin', label: '知识库管理员' },
  { value: 'sys_admin', label: '系统管理员' },
]
