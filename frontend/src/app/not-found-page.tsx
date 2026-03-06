import { Link } from 'react-router-dom'
import { Button } from '../components/ui/button'

export function NotFoundPage() {
  return (
    <main className="grid min-h-screen place-items-center bg-[var(--color-background)] p-[var(--space-4)]">
      <section className="max-w-lg text-center">
        <p className="text-sm font-semibold text-[var(--color-primary)]">404</p>
        <h1 className="mt-[var(--space-2)] text-2xl font-semibold text-[var(--color-text-primary)]">页面不存在</h1>
        <p className="mt-[var(--space-2)] text-sm leading-[22px] text-[var(--color-text-secondary)]">
          请求的地址不存在，请返回前端基础页继续操作。
        </p>
        <div className="mt-[var(--space-6)] flex justify-center">
          <Button asChild><Link to="/">返回首页</Link></Button>
        </div>
      </section>
    </main>
  )
}
