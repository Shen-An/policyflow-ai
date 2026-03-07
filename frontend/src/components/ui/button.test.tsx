import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Button } from './button'

describe('Button', () => {
  it('is keyboard accessible and keeps native button semantics', async () => {
    const onClick = vi.fn()
    const user = userEvent.setup()
    render(<Button onClick={onClick}>继续</Button>)
    const button = screen.getByRole('button', { name: '继续' })
    button.focus()
    await user.keyboard('{Enter}')
    expect(onClick).toHaveBeenCalledOnce()
  })

  it('delegates semantics to a child link through Radix Slot', () => {
    render(<Button asChild><a href="/health">检查状态</a></Button>)
    expect(screen.getByRole('link', { name: '检查状态' })).toHaveAttribute('href', '/health')
  })
})
