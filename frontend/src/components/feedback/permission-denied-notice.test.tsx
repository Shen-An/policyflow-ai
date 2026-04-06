import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PermissionDeniedNotice } from './permission-denied-notice'

describe('PermissionDeniedNotice', () => {
  it('provides explicit page and operation-level feedback', () => {
    const { rerender } = render(<PermissionDeniedNotice />)
    expect(screen.getByRole('alert')).toHaveTextContent('你没有访问此功能的权限')
    rerender(<PermissionDeniedNotice compact />)
    expect(screen.getByRole('alert')).toBeVisible()
  })
})
