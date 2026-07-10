import { Slot } from '@radix-ui/react-slot'
import type { ButtonHTMLAttributes } from 'react'
import { cn } from '../../lib/cn'

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { asChild?: boolean }

export function Button({ asChild = false, className, type = 'button', ...props }: ButtonProps) {
  const Component = asChild ? Slot : 'button'
  return (
    <Component
      className={cn(
        'inline-flex min-h-10 items-center justify-center gap-[var(--space-2)] rounded-md bg-[var(--color-primary)] px-[var(--space-4)] py-[var(--space-2)] text-sm font-semibold text-white transition-colors hover:bg-[var(--color-primary-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary)] focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60 motion-reduce:transition-none',
        className,
      )}
      type={asChild ? undefined : type}
      {...props}
    />
  )
}
