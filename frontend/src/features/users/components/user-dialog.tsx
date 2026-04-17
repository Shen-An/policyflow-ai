import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import type { PropsWithChildren, ReactNode } from 'react'

export function UserDialog({ open, onOpenChange, title, description, children, footer }: PropsWithChildren<{
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  footer: ReactNode
}>) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-slate-950/50" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[calc(100%-32px)] max-w-xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-[var(--space-6)] shadow-xl focus:outline-none">
          <Dialog.Title className="text-lg font-semibold leading-7">{title}</Dialog.Title>
          <Dialog.Description className="mt-[var(--space-1)] text-sm leading-[22px] text-[var(--color-text-secondary)]">{description}</Dialog.Description>
          <Dialog.Close className="absolute right-[var(--space-4)] top-[var(--space-4)] inline-flex size-9 items-center justify-center rounded-md text-[var(--color-text-secondary)] hover:bg-slate-100" aria-label="关闭对话框"><X aria-hidden="true" className="size-5" /></Dialog.Close>
          <div className="mt-[var(--space-6)]">{children}</div>
          <div className="mt-[var(--space-6)] flex justify-end gap-[var(--space-3)]">{footer}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
