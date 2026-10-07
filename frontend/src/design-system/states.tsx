import type { ReactNode } from 'react'
import { Button } from 'antd'
import {
  ArrowClockwise,
  CloudSlash,
  Lock,
  Prohibit,
  Tray,
  Warning,
  WifiHigh,
} from '@phosphor-icons/react'

// Stage 8 shared runtime-state components (T128). Every state gives the user a clear
// reason AND an executable next step. Consistent surface, accessible roles/live regions,
// AA-contrast text. Used across chat / workspace / approval / knowledge / memory so the
// desktop app never leaves the user at a dead end.

export type StateTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger'

export type StateAction = {
  label: string
  onClick?: () => void
  /** Marked as the primary next step; rendered as the `state-action` affordance. */
  primary?: boolean
  danger?: boolean
}

type StatePanelProps = {
  testId: string
  tone?: StateTone
  icon: ReactNode
  title: string
  description?: ReactNode
  /** Live-region politeness: 'assertive' for errors/permission, 'polite' otherwise. */
  live?: 'polite' | 'assertive'
  role?: 'status' | 'alert'
  actions?: StateAction[]
  variant?: 'block' | 'inline'
  children?: ReactNode
}

const toneColor: Record<StateTone, string> = {
  neutral: 'var(--ds-text-muted)',
  info: 'var(--ds-info)',
  success: 'var(--ds-success)',
  warning: 'var(--ds-warning)',
  danger: 'var(--ds-danger)',
}

const toneSoft: Record<StateTone, string> = {
  neutral: 'var(--ds-card-muted)',
  info: 'var(--ds-info-soft)',
  success: 'var(--ds-success-soft)',
  warning: 'var(--ds-warning-soft)',
  danger: 'var(--ds-danger-soft)',
}

/** Base layout for all runtime states. Not exported; use the named states below. */
function StatePanel({
  testId,
  tone = 'neutral',
  icon,
  title,
  description,
  live = 'polite',
  role = 'status',
  actions = [],
  variant = 'block',
  children,
}: StatePanelProps) {
  const primary = actions.find((a) => a.primary) ?? actions[0]
  const rest = actions.filter((a) => a !== primary)
  return (
    <div
      data-testid={testId}
      role={role}
      aria-live={live}
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        textAlign: 'center',
        gap: 'var(--ds-space-3)',
        padding: variant === 'block' ? 'var(--ds-space-10) var(--ds-space-6)' : 'var(--ds-space-5)',
        minHeight: variant === 'block' ? 280 : undefined,
        color: 'var(--ds-text)',
      }}
    >
      <span
        aria-hidden
        style={{
          display: 'grid',
          placeItems: 'center',
          width: 48,
          height: 48,
          borderRadius: 'var(--ds-radius-lg)',
          background: toneSoft[tone],
          color: toneColor[tone],
        }}
      >
        {icon}
      </span>
      <div style={{ fontSize: 'var(--ds-text-lg)', fontWeight: 650, color: 'var(--ds-text)' }}>
        {title}
      </div>
      {description ? (
        <div
          style={{
            fontSize: 'var(--ds-text-base)',
            lineHeight: 'var(--ds-leading)',
            color: 'var(--ds-text-secondary)',
            maxWidth: 420,
          }}
        >
          {description}
        </div>
      ) : null}
      {children}
      {actions.length > 0 ? (
        <div style={{ display: 'flex', gap: 'var(--ds-space-2)', marginTop: 'var(--ds-space-2)' }}>
          {primary ? (
            <Button
              type="primary"
              danger={primary.danger}
              onClick={primary.onClick}
              data-testid="state-action"
            >
              {primary.label}
            </Button>
          ) : null}
          {rest.map((action) => (
            <Button key={action.label} onClick={action.onClick} danger={action.danger}>
              {action.label}
            </Button>
          ))}
        </div>
      ) : null}
    </div>
  )
}

export function LoadingState({
  title = '正在加载…',
  description,
  variant,
}: {
  title?: string
  description?: ReactNode
  variant?: 'block' | 'inline'
}) {
  return (
    <StatePanel
      testId="state-loading"
      tone="info"
      role="status"
      live="polite"
      variant={variant}
      icon={<ArrowClockwise size={24} weight="bold" className="ds-spin" />}
      title={title}
      description={description}
    />
  )
}

export function EmptyState({
  title,
  description,
  action,
  icon,
  variant,
  children,
}: {
  title: string
  description?: ReactNode
  action?: StateAction
  icon?: ReactNode
  variant?: 'block' | 'inline'
  children?: ReactNode
}) {
  return (
    <StatePanel
      testId="state-empty"
      tone="neutral"
      role="status"
      variant={variant}
      icon={icon ?? <Tray size={24} weight="duotone" />}
      title={title}
      description={description}
      actions={action ? [{ ...action, primary: true }] : []}
    >
      {children}
    </StatePanel>
  )
}

export function ErrorState({
  title = '出错了',
  description = '操作未能完成。你可以重试，若反复失败请联系管理员。',
  onRetry,
  retryLabel = '重试',
  variant,
}: {
  title?: string
  description?: ReactNode
  onRetry?: () => void
  retryLabel?: string
  variant?: 'block' | 'inline'
}) {
  return (
    <StatePanel
      testId="state-error"
      tone="danger"
      role="alert"
      live="assertive"
      variant={variant}
      icon={<Warning size={24} weight="duotone" />}
      title={title}
      description={description}
      actions={onRetry ? [{ label: retryLabel, onClick: onRetry, primary: true }] : []}
    />
  )
}

export function OfflineState({
  title = '网络已断开',
  description = '网络连接中断，现有内容仍会保留。恢复网络后可重试当前操作。',
  onRetry,
  variant,
}: {
  title?: string
  description?: ReactNode
  onRetry?: () => void
  variant?: 'block' | 'inline'
}) {
  return (
    <StatePanel
      testId="state-offline"
      tone="warning"
      role="status"
      live="polite"
      variant={variant ?? 'inline'}
      icon={<CloudSlash size={24} weight="duotone" />}
      title={title}
      description={description}
      actions={onRetry ? [{ label: '重试', onClick: onRetry, primary: true }] : []}
    />
  )
}

export function RecoveryState({
  title = '连接已恢复',
  description = '网络已恢复，可以继续操作或刷新最新内容。',
  onRetry,
  variant,
}: {
  title?: string
  description?: ReactNode
  onRetry?: () => void
  variant?: 'block' | 'inline'
}) {
  return (
    <StatePanel
      testId="state-recovery"
      tone="success"
      role="status"
      live="polite"
      variant={variant ?? 'inline'}
      icon={<WifiHigh size={24} weight="duotone" />}
      title={title}
      description={description}
      actions={onRetry ? [{ label: '刷新', onClick: onRetry, primary: true }] : []}
    />
  )
}

export function PermissionState({
  title = '没有权限',
  description = '你当前的角色无权执行此操作。如确有需要，请联系管理员申请授权。',
  action,
  variant,
}: {
  title?: string
  description?: ReactNode
  action?: StateAction
  variant?: 'block' | 'inline'
}) {
  return (
    <StatePanel
      testId="state-permission"
      tone="warning"
      role="alert"
      live="assertive"
      variant={variant}
      icon={<Lock size={24} weight="duotone" />}
      title={title}
      description={description}
      actions={action ? [{ ...action, primary: true }] : []}
    />
  )
}

export function ConflictState({
  title = '内容已变化',
  description = '相关材料或审批请求已被更新，之前的结果已失效。请刷新后基于最新内容重新操作。',
  action,
  variant,
}: {
  title?: string
  description?: ReactNode
  action?: StateAction
  variant?: 'block' | 'inline'
}) {
  return (
    <StatePanel
      testId="state-conflict"
      tone="warning"
      role="alert"
      live="assertive"
      variant={variant}
      icon={<Prohibit size={24} weight="duotone" />}
      title={title}
      description={description}
      actions={action ? [{ ...action, primary: true }] : []}
    />
  )
}
