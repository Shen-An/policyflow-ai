import { zodResolver } from '@hookform/resolvers/zod'
import { LogIn } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useForm } from 'react-hook-form'
import { login } from '../../api/auth'
import { AppError } from '../../api/errors'
import { authStore } from '../../auth/auth-store'
import { Alert } from '../../components/feedback/alert'
import { Button } from '../../components/ui/button'
import { loginSchema, type LoginFormValues } from './login-schema'

function errorMessage(error: unknown): string {
  if (error instanceof AppError && error.code === 'AUTH_INVALID_CREDENTIALS') {
    return '用户名或密码不正确，请检查后重新输入。'
  }
  if (error instanceof AppError) return error.message
  return '登录未能完成，请稍后重试。'
}

export function LoginPage() {
  const [summary, setSummary] = useState<string | null>(null)
  const summaryRef = useRef<HTMLDivElement>(null)
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<LoginFormValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { username: '', password: '' },
  })

  useEffect(() => {
    if (summary) summaryRef.current?.focus()
  }, [summary])

  async function submit(values: LoginFormValues) {
    setSummary(null)
    try {
      const result = await login(values)
      authStore.authenticateForDuration(result.accessToken, result.expiresIn, result.user)
    } catch (error) {
      setSummary(errorMessage(error))
    }
  }

  const onSubmit = handleSubmit(submit)

  return (
    <main className="grid min-h-screen place-items-center bg-[var(--color-background)] p-[var(--space-4)]">
      <section className="w-full max-w-md rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-[var(--space-8)] shadow-sm">
        <div className="flex size-10 items-center justify-center rounded-lg bg-[var(--color-primary-50)] text-[var(--color-primary)]"><LogIn aria-hidden="true" className="size-5" /></div>
        <h1 className="mt-[var(--space-4)] text-2xl font-semibold leading-8">登录 PolicyFlow AI</h1>
        <p className="mt-[var(--space-2)] text-sm leading-[22px] text-[var(--color-text-secondary)]">使用组织账户继续访问企业政策助手。</p>
        {summary ? <div ref={summaryRef} role="alert" tabIndex={-1}><Alert role="status" tone="danger">{summary}</Alert></div> : null}
        <form className="mt-[var(--space-6)] space-y-[var(--space-4)]" onSubmit={onSubmit} noValidate>
          <div>
            <label className="text-sm font-semibold" htmlFor="username">用户名</label>
            <input id="username" autoComplete="username" aria-invalid={Boolean(errors.username)} aria-describedby={errors.username ? 'username-error' : undefined} className="mt-[var(--space-2)] min-h-11 w-full rounded-md border border-[var(--color-border)] px-[var(--space-3)] text-base focus:border-[var(--color-primary)] sm:text-sm" {...register('username')} />
            {errors.username ? <p id="username-error" className="mt-[var(--space-1)] text-xs text-[var(--color-danger)]">{errors.username.message}</p> : null}
          </div>
          <div>
            <label className="text-sm font-semibold" htmlFor="password">密码</label>
            <input id="password" type="password" autoComplete="current-password" aria-invalid={Boolean(errors.password)} aria-describedby={errors.password ? 'password-error' : undefined} className="mt-[var(--space-2)] min-h-11 w-full rounded-md border border-[var(--color-border)] px-[var(--space-3)] text-base focus:border-[var(--color-primary)] sm:text-sm" {...register('password')} />
            {errors.password ? <p id="password-error" className="mt-[var(--space-1)] text-xs text-[var(--color-danger)]">{errors.password.message}</p> : null}
          </div>
          <Button className="w-full" type="submit" disabled={isSubmitting}>{isSubmitting ? '正在登录…' : '登录'}</Button>
        </form>
      </section>
    </main>
  )
}
