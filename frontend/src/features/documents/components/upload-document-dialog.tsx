import { UploadSimple, X } from '@phosphor-icons/react'
import * as Dialog from '@radix-ui/react-dialog'
import { useState } from 'react'
import { AppError } from '../../../api/errors'
import { Button } from '../../../components/ui/button'
import { Alert } from '../../../components/feedback/alert'
import { validateDocumentFile } from '../file-validation'
import { useUploadDocumentMutation } from '../queries'

export function UploadDocumentDialog({
  knowledgeBaseId,
  open,
  onOpenChange,
}: {
  knowledgeBaseId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const mutation = useUploadDocumentMutation(knowledgeBaseId)
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [error, setError] = useState<string | null>(null)

  function changeOpen(next: boolean) {
    if (!next && !mutation.isPending) {
      setFile(null)
      setTitle('')
      setError(null)
    }
    onOpenChange(next)
  }

  async function submit() {
    if (!file) {
      setError('请选择要上传的文件。')
      return
    }
    const validation = validateDocumentFile(file)
    if (validation) {
      setError(validation)
      return
    }
    setError(null)
    try {
      await mutation.mutateAsync({ file, title: title || undefined })
      changeOpen(false)
    } catch (reason) {
      if (reason instanceof AppError) {
        if (reason.code === 'DOCUMENT_DUPLICATE') {
          setError('同一知识库中已经存在内容相同的文档。')
        } else if (reason.code === 'DOCUMENT_TYPE_NOT_SUPPORTED') {
          setError('后端不支持该文件类型。')
        } else if (reason.code === 'DOCUMENT_TOO_LARGE') {
          setError('文件超过后端允许的大小。')
        } else {
          setError(reason.message)
        }
      } else {
        setError('文档上传失败，请稍后重试。')
      }
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={changeOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-slate-950/50" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-32px)] max-w-lg -translate-x-1/2 -translate-y-1/2 rounded-xl border border-[var(--color-border)] bg-white p-[var(--space-6)] shadow-xl focus:outline-none">
          <Dialog.Title className="text-lg font-semibold">上传文档</Dialog.Title>
          <Dialog.Description className="mt-[var(--space-1)] text-sm text-[var(--color-text-secondary)]">
            支持 TXT、Markdown、DOCX 和 PDF，单文件最大 20MB。
          </Dialog.Description>
          <Dialog.Close
            aria-label="关闭对话框"
            className="absolute right-[var(--space-4)] top-[var(--space-4)] inline-flex size-9 items-center justify-center rounded-md hover:bg-slate-100"
          >
            <X size={16} weight="regular" className="size-5" aria-hidden="true" />
          </Dialog.Close>

          {error ? (
            <Alert tone="danger" className="mt-[var(--space-4)]">{error}</Alert>
          ) : null}

          <div className="mt-[var(--space-6)] space-y-[var(--space-4)]">
            <label className="block text-sm font-semibold">
              文件
              <input
                type="file"
                accept=".txt,.md,.docx,.pdf"
                className="mt-[var(--space-2)] block w-full rounded-md border border-[var(--color-border)] p-[var(--space-3)] font-normal"
                onChange={(event) => {
                  const selected = event.target.files?.[0] ?? null
                  setFile(selected)
                  setError(selected ? validateDocumentFile(selected) : null)
                }} />
            </label>
            <label className="block text-sm font-semibold">
              标题（可选）
              <input
                value={title}
                maxLength={255}
                onChange={(event) => setTitle(event.target.value)}
                className="mt-[var(--space-2)] min-h-10 w-full rounded-md border border-[var(--color-border)] px-[var(--space-3)] font-normal" />
            </label>
          </div>

          <div className="mt-[var(--space-6)] flex justify-end gap-[var(--space-3)]">
            <Button
              variant="secondary"
              onClick={() => changeOpen(false)}
              disabled={mutation.isPending}
            >
              取消
            </Button>
            <Button onClick={submit} disabled={mutation.isPending || Boolean(file && error)}>
              <UploadSimple size={16} weight="duotone" className="size-4" aria-hidden="true" />
              {mutation.isPending ? '正在上传…' : '上传文档'}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
