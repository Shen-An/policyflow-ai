import type { FieldValues, Path, UseFormSetError } from 'react-hook-form'
import { AppError } from '../../api/errors'

type ValidationDetail = { loc?: Array<string | number>; msg?: string }

export function applyValidationErrors<T extends FieldValues>(error: unknown, setError: UseFormSetError<T>, fieldMap: Record<string, Path<T>>): boolean {
  if (!(error instanceof AppError) || error.status !== 422 || !Array.isArray(error.details)) return false
  let applied = false
  for (const detail of error.details as ValidationDetail[]) {
    const backendField = detail.loc?.at(-1)
    if (typeof backendField !== 'string' || !fieldMap[backendField]) continue
    setError(fieldMap[backendField], { type: 'server', message: detail.msg ?? '字段校验失败' })
    applied = true
  }
  return applied
}
