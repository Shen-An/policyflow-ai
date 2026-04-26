import { zodResolver } from '@hookform/resolvers/zod'
import { cloneElement, useState, type ReactElement } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { z } from 'zod'
import type { RoleCode } from '../../../api/auth'
import { AppError } from '../../../api/errors'
import { Button } from '../../../components/ui/button'
import { Alert } from '../../../components/feedback/alert'
import { applyValidationErrors } from '../form-errors'
import { useCreateUserMutation } from '../queries'
import { roleOptions } from '../role-options'
import { UserDialog } from './user-dialog'

const schema = z.object({
  username: z.string().trim().min(3, '用户名至少 3 个字符').max(64).regex(/^[A-Za-z0-9_.-]+$/u, '仅允许字母、数字、点、下划线和短横线'),
  email: z.string().trim().min(3).max(255).email('请输入有效邮箱'),
  displayName: z.string().trim().min(1, '请输入显示名').max(100),
  password: z.string().min(8, '密码至少 8 个字符').max(128),
  departmentId: z.string().trim().optional(),
  roleCodes: z.array(z.enum(['employee', 'kb_admin', 'sys_admin'])).min(1, '至少选择一个角色'),
})
type Values = z.infer<typeof schema>

const defaults: Values = { username: '', email: '', displayName: '', password: '', departmentId: '', roleCodes: ['employee'] }

export function CreateUserDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const mutation = useCreateUserMutation()
  const [summary, setSummary] = useState<string | null>(null)
  const { register, handleSubmit, control, setValue, setError, reset, formState: { errors } } = useForm<Values>({ resolver: zodResolver(schema), defaultValues: defaults })
  const selectedRoles = useWatch({ control, name: 'roleCodes' })

  function close(next: boolean) {
    if (!next && !mutation.isPending) { reset(defaults); setSummary(null) }
    onOpenChange(next)
  }

  async function submit(values: Values) {
    setSummary(null)
    try {
      await mutation.mutateAsync({
        username: values.username,
        email: values.email,
        displayName: values.displayName,
        password: values.password,
        departmentId: values.departmentId || undefined,
        roleCodes: values.roleCodes,
      })
      close(false)
    } catch (error) {
      if (error instanceof AppError && error.status === 409) {
        const field = error.code === 'USER_EMAIL_EXISTS' ? 'email' : 'username'
        setError(field, { type: 'server', message: error.code === 'USER_EMAIL_EXISTS' ? '该邮箱已存在' : '该用户名已存在' })
        setSummary('用户创建失败，请修正冲突字段。')
        return
      }
      if (applyValidationErrors(error, setError, { username: 'username', email: 'email', display_name: 'displayName', password: 'password', department_id: 'departmentId', role_codes: 'roleCodes' })) {
        setSummary('部分字段未通过服务端校验，请检查后重试。')
        return
      }
      setSummary(error instanceof AppError ? error.message : '用户创建失败，请稍后重试。')
    }
  }

  function toggleRole(role: RoleCode) {
    const next = selectedRoles.includes(role) ? selectedRoles.filter((value) => value !== role) : [...selectedRoles, role]
    setValue('roleCodes', next, { shouldValidate: true })
  }

  return (
    <UserDialog open={open} onOpenChange={close} title="创建用户" description="字段严格对应当前后端 UserCreate 契约。" footer={<><Button variant="secondary" onClick={() => close(false)} disabled={mutation.isPending}>取消</Button><Button type="submit" form="create-user-form" disabled={mutation.isPending}>{mutation.isPending ? '正在创建…' : '创建用户'}</Button></>}>
      {summary ? <Alert tone="danger" className="mb-[var(--space-4)]">{summary}</Alert> : null}
      <form id="create-user-form" className="grid gap-[var(--space-4)] sm:grid-cols-2" onSubmit={handleSubmit(submit)} noValidate>
        <Field name="username" label="用户名" error={errors.username?.message}><input className="mt-[var(--space-2)] min-h-10 w-full rounded-md border border-[var(--color-border)] px-[var(--space-3)] font-normal" autoComplete="off" {...register('username')} /></Field>
        <Field name="email" label="邮箱" error={errors.email?.message}><input className="mt-[var(--space-2)] min-h-10 w-full rounded-md border border-[var(--color-border)] px-[var(--space-3)] font-normal" type="email" autoComplete="off" {...register('email')} /></Field>
        <Field name="display-name" label="显示名" error={errors.displayName?.message}><input className="mt-[var(--space-2)] min-h-10 w-full rounded-md border border-[var(--color-border)] px-[var(--space-3)] font-normal" autoComplete="off" {...register('displayName')} /></Field>
        <Field name="password" label="初始密码" error={errors.password?.message}><input className="mt-[var(--space-2)] min-h-10 w-full rounded-md border border-[var(--color-border)] px-[var(--space-3)] font-normal" type="password" autoComplete="new-password" {...register('password')} /></Field>
        <Field name="department-id" label="部门 ID（可选）" error={errors.departmentId?.message}><input className="mt-[var(--space-2)] min-h-10 w-full rounded-md border border-[var(--color-border)] px-[var(--space-3)] font-normal" autoComplete="off" {...register('departmentId')} /></Field>
        <fieldset className="sm:col-span-2"><legend className="text-sm font-semibold">角色</legend><div className="mt-[var(--space-2)] flex flex-wrap gap-[var(--space-4)]">{roleOptions.map((role) => <label key={role.value} className="flex items-center gap-[var(--space-2)] text-sm"><input type="checkbox" checked={selectedRoles.includes(role.value)} onChange={() => toggleRole(role.value)} />{role.label}</label>)}</div>{errors.roleCodes ? <p className="mt-[var(--space-1)] text-xs text-[var(--color-danger)]">{errors.roleCodes.message}</p> : null}</fieldset>
      </form>
    </UserDialog>
  )
}

function Field({ name, label, error, children }: { name: string; label: string; error?: string; children: ReactElement<Record<string, unknown>> }) {
  const inputId = `create-user-${name}`
  const errorId = `${inputId}-error`
  return <div><label className="text-sm font-semibold" htmlFor={inputId}>{label}</label>{cloneElement(children, { id: inputId, 'aria-invalid': Boolean(error), 'aria-describedby': error ? errorId : undefined })}{error ? <p id={errorId} className="mt-[var(--space-1)] text-xs text-[var(--color-danger)]">{error}</p> : null}</div>
}
