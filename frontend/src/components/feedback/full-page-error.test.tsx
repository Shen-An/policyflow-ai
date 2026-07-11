import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { FullPageError } from './full-page-error'

describe('FullPageError', () => {
  it('keeps the error visible and exposes a keyboard retry action', async () => {
    const retry = vi.fn()
    const user = userEvent.setup()
    render(<FullPageError message="网络连接失败" onRetry={retry} />)
    expect(screen.getByRole('alert')).toHaveTextContent('网络连接失败')
    await user.click(screen.getByRole('button', { name: '重新尝试' }))
    expect(retry).toHaveBeenCalledOnce()
  })
})
