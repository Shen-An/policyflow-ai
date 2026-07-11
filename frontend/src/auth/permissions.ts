import type { RoleCode } from '../api/auth'

export function hasAnyRole(actual: RoleCode[], required: RoleCode[]): boolean {
  if (required.length === 0) return true
  return required.some((role) => actual.includes(role))
}
