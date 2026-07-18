import { X } from '@phosphor-icons/react'
import { zodResolver } from '@hookform/resolvers/zod'
import * as Dialog from '@radix-ui/react-dialog'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { AppError } from '../../../api/errors'
import { Button } from '../../../components/ui/button'
import { Alert } from '../../../components/feedback/alert'
import {
  useCreateKnowledgeBaseMutation,
  useCreateOptionsQuery,
} from '../queries'

const schema = z.object({
  name: z.string().trim().min(1, '请输入知识库名称').max(100),
  code: z
    .string()
    .trim()
    .min(2, '编码至少 2 个字符')
    .max(50)
    .regex(/^[a-z0-9_-]+$/u, '仅允许小写字母、数字、下划线和短横线'),
  departmentId: z.string().min(1, '请选择部门'),
  description: z.string().max(1000, '描述不能超过 1000 个字符'),
  defaultQueryMode: z.enum(['naive', 'local', 'global', 'hybrid', 'mix']),
})

type Values = z.infer<typeof schema>

const defaults: Values = {
  name: '',
  code: '',
  departmentId: '',
  description: '',
  defaultQueryMode: 'mix',
}

export function CreateKnowledgeBaseDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const options = useCreateOptionsQuery(open)
  const mutation = useCreateKnowledgeBaseMutation()
  const [summary, setSummary] = useState<string | null>(null)
  const {
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: defaults,
  })

  function changeOpen(next: boolean) {
    if (!next && !mutation.isPending) {
      reset(defaults)
      setSummary(null)
    }
    onOpenChange(next)
  }

  async function submit(values: Values) {
    setSummary(null)
    try {
      await mutation.mutateAsync(values)
      changeOpen(false)
    } catch (error) {
      if (error instanceof AppError && error.code === 'KB_CODE_EXISTS') {
        setError('code', { type: 'server', message: '该知识库编码已存在' })
        setSummary('创建失败，请修改冲突字段。')
        return
      }
      setSummary(
        error instanceof AppError
          ? error.message
          : '知识库创建失败，请稍后重试。',
      )
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={changeOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-slate-950/50" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[calc(100%-32px)] max-w-xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-[var(--space-6)] shadow-xl focus:outline-none">
          <Dialog.Title className="text-lg font-semibold">创建知识库</Dialog.Title>
          <Dialog.Description className="mt-[var(--space-1)] text-sm text-[var(--color-text-secondary)]">
            创建后，所选部门默认获得读取权限。
          </Dialog.Description>
          <Dialog.Close
            aria-label="关闭对话框"
            className="absolute right-[var(--space-4)] top-[var(--space-4)] inline-flex size-9 items-center justify-center rounded-md hover:bg-slate-100"
          >
            <X size={16} weight="regular" className="size-5" aria-hidden="true" />
          </Dialog.Close>

          {summary ? (
            <Alert tone="danger" className="mt-[var(--space-4)]">{summary}</Alert>
          ) : null}

          <form
            id="create-kb-form"
            className="mt-[var(--space-6)] grid gap-[var(--space-4)] sm:grid-cols-2"
            onSubmit={handleSubmit(submit)}
            noValidate
          >
            <Field label="名称" error={errors.name?.message}>
              <input {...register('name')} />
            </Field>
            <Field label="编码" error={errors.code?.message}>
              <input {...register('code')} />
            </Field>
            <Field label="部门" error={errors.departmentId?.message}>
              <select {...register('departmentId')} disabled={options.isPending}>
                <option value="">请选择部门</option>
                {options.data?.map((department) => (
                  <option key={department.id} value={department.id}>
                    {department.name}（{department.code}）
                  </option>
                ))}
              </select>
            </Field>
            <Field label="默认检索模式" error={errors.defaultQueryMode?.message}>
              <select {...register('defaultQueryMode')}>
                <option value="mix">mix</option>
                <option value="hybrid">hybrid</option>
                <option value="local">local</option>
                <option value="global">global</option>
                <option value="naive">naive</option>
              </select>
            </Field>
            <label className="text-sm font-semibold sm:col-span-2">
              描述
              <textarea
                rows={4}
                className="mt-[var(--space-2)] w-full rounded-md border border-[var(--color-border)] p-[var(--space-3)] font-normal"
                {...register('description')} />
              {errors.description ? (
                <span className="mt-[var(--space-1)] block text-xs font-normal text-[var(--color-danger)]">
                  {errors.description.message}
                </span>
              ) : null}
            </label>
          </form>

          <div className="mt-[var(--space-6)] flex justify-end gap-[var(--space-3)]">
            <Button
              variant="secondary"
              onClick={() => changeOpen(false)}
              disabled={mutation.isPending}
            >
              取消
            </Button>
            <Button
              type="submit"
              form="create-kb-form"
              disabled={mutation.isPending || options.isPending || options.isError}
            >
              {mutation.isPending ? '正在创建…' : '创建知识库'}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function Field({
  label,
  error,
  children,
}: {
  label: string
  error?: string
  children: React.ReactElement
}) {
  return (
    <label className="text-sm font-semibold">
      {label}
      {/*
        Inputs are nested in their label so validation text remains associated
        without introducing feature-global form abstractions.
      */}
      {children}
      {error ? (
        <span className="mt-[var(--space-1)] block text-xs font-normal text-[var(--color-danger)]">
          {error}
        </span>
      ) : null}
    </label>
  )
}
