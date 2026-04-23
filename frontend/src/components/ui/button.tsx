import { Slot } from '@radix-ui/react-slot'
import type { ButtonHTMLAttributes } from 'react'
import { cn } from '../../lib/cn'

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  asChild?: boolean
  variant?: ButtonVariant
}

const variantStyles: Record<ButtonVariant, string> = {
  primary: 'bg-[var(--color-primary)] text-white hover:bg-[var(--color-primary-hover)]',
  secondary:
    'bg-[var(--color-surface)] text-[var(--color-text-primary)] ring-1 ring-inset ring-[var(--color-border)] hover:bg-[var(--color-background)]',
  ghost:
    'bg-transparent text-[var(--color-text-primary)] hover:bg-[var(--color-background)]',
  danger: 'bg-[var(--color-danger)] text-white hover:bg-[var(--color-danger-hover)]',
}

export function Button({ asChild = false, variant = 'primary', className, type = 'button', ...props }: ButtonProps) {
  const Component = asChild ? Slot : 'button'
  return (
    <Component
      className={cn(
        'inline-flex min-h-11 items-center justify-center gap-[var(--space-2)] rounded-md px-[var(--space-4)] py-[var(--space-2)] text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary)] focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60 motion-reduce:transition-none',
        variantStyles[variant],
        className,
      )}
      type={asChild ? undefined : type}
      {...props}
    />
  )
}
