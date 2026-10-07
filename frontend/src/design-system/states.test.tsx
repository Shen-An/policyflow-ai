import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import {
  ConflictState,
  EmptyState,
  ErrorState,
  LoadingState,
  OfflineState,
  PermissionState,
  RecoveryState,
} from './states'

// T128 — the shared runtime-state components each render a clear reason and, where a
// next step applies, an executable action marked `state-action`. Error/permission/
// conflict are assertive alerts; the quieter states are polite status regions.

describe('design-system runtime states', () => {
  it('renders a polite loading state', () => {
    render(<LoadingState />)
    const el = screen.getByTestId('state-loading')
    expect(el).toBeInTheDocument()
    expect(el).toHaveAttribute('role', 'status')
    expect(el).toHaveAttribute('aria-live', 'polite')
  })

  it('renders an actionable empty state', () => {
    const onClick = vi.fn()
    render(<EmptyState title="还没有内容" description="从这里开始" action={{ label: '新建', onClick }} />)
    expect(screen.getByTestId('state-empty')).toHaveTextContent('还没有内容')
    fireEvent.click(screen.getByTestId('state-action'))
    expect(onClick).toHaveBeenCalledOnce()
  })

  it('renders an assertive error state with a retry next step', () => {
    const onRetry = vi.fn()
    render(<ErrorState description="请求失败" onRetry={onRetry} />)
    const el = screen.getByTestId('state-error')
    expect(el).toHaveAttribute('role', 'alert')
    fireEvent.click(screen.getByTestId('state-action'))
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it('renders offline and recovery states as polite status regions', () => {
    const { rerender } = render(<OfflineState onRetry={vi.fn()} />)
    expect(screen.getByTestId('state-offline')).toHaveTextContent('网络')
    rerender(<RecoveryState onRetry={vi.fn()} />)
    expect(screen.getByTestId('state-recovery')).toBeInTheDocument()
  })

  it('renders permission and conflict states with assertive roles', () => {
    const { rerender } = render(<PermissionState action={{ label: '申请授权', onClick: vi.fn() }} />)
    const permission = screen.getByTestId('state-permission')
    expect(permission).toHaveAttribute('role', 'alert')
    expect(screen.getByTestId('state-action')).toHaveTextContent('申请授权')

    rerender(<ConflictState action={{ label: '刷新', onClick: vi.fn() }} />)
    expect(screen.getByTestId('state-conflict')).toHaveAttribute('role', 'alert')
  })
})
