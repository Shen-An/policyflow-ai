import { useState } from 'react'
import type { RoleCode } from '../../../api/auth'
import type { UserRecord } from '../../../api/users'
import { AppError } from '../../../api/errors'
import { Button } from '../../../components/ui/button'
import { useUpdateRolesMutation } from '../queries'
import { roleOptions } from '../role-options'
import { UserDialog } from './user-dialog'

export function EditRolesDialog({ user, open, onOpenChange }: { user: UserRecord; open: boolean; onOpenChange: (open: boolean) => void }) {
  const [roles, setRoles] = useState<RoleCode[]>(user.roles)
  const [error, setError] = useState<string | null>(null)
  const mutation = useUpdateRolesMutation()

  function toggle(role: RoleCode) {
    setError(null)
    setRoles((current) => current.includes(role) ? current.filter((value) => value !== role) : [...current, role])
  }

  async function submit() {
    if (roles.length === 0) { setError('至少保留一个角色。'); return }
    try {
      await mutation.mutateAsync({ userId: user.id, roleCodes: roles })
      onOpenChange(false)
    } catch (reason) {
      setError(reason instanceof AppError ? reason.message : '角色更新失败，请稍后重试。')
    }
  }

  return (
    <UserDialog open={open} onOpenChange={(next) => { if (!mutation.isPending) onOpenChange(next) }} title="修改用户角色" description={`正在修改 ${user.displayName}（${user.username}）的角色。`} footer={<><Button className="bg-white text-[var(--color-text-primary)] ring-1 ring-[var(--color-border)] hover:bg-slate-50" onClick={() => onOpenChange(false)} disabled={mutation.isPending}>取消</Button><Button onClick={submit} disabled={mutation.isPending}>{mutation.isPending ? '正在保存…' : '保存角色'}</Button></>}>
      {error ? <div role="alert" className="mb-[var(--space-4)] rounded-md border border-red-200 bg-red-50 p-[var(--space-3)] text-sm text-[var(--color-danger)]">{error}</div> : null}
      <fieldset><legend className="text-sm font-semibold">角色</legend><div className="mt-[var(--space-3)] grid gap-[var(--space-3)] sm:grid-cols-3">{roleOptions.map((role) => <label key={role.value} className="flex items-center gap-[var(--space-2)] rounded-md border border-[var(--color-border)] p-[var(--space-3)] text-sm"><input type="checkbox" checked={roles.includes(role.value)} onChange={() => toggle(role.value)} />{role.label}</label>)}</div></fieldset>
    </UserDialog>
  )
}
