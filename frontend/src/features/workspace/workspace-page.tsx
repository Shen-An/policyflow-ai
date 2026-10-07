import { useCallback, useState } from 'react'
import { ArrowLeft, FileArrowUp, FolderOpen, Receipt } from '@phosphor-icons/react'
import { desktopApi } from '../../services/desktop-api'
import { EmptyState } from '../../design-system/states'
import { useWorkflow } from './workflow-store'
import { WorkflowPage } from './workflow-page'

// T134 — the reimbursement workspace entry: choose an authorized material, then start the
// file workflow. Selection declares the material and binds a task workspace through the
// bridge; once a run is active the integrated workflow (tree / preview / version / diff /
// approval / result) is shown inline. A clearly-labelled demo-scenario launcher makes the
// permission / conflict / failure states reproducible for review.

type AuthorizedMaterial = { id: string; name: string; purpose: string; hash: string }

const MATERIALS: AuthorizedMaterial[] = [
  {
    id: 'm1',
    name: '2026年1月 出差行程与票据',
    purpose: '差旅报销',
    hash: 'a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90',
  },
  {
    id: 'm2',
    name: '市内交通费票据（1月）',
    purpose: '交通费报销',
    hash: 'b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1',
  },
]

const SCENARIOS: Array<{ key: string; label: string; hint: string }> = [
  { key: 'permission', label: '演示：无审批权限', hint: '审批时返回 403 的权限边界' },
  { key: 'conflict', label: '演示：材料已变更', hint: '审批时返回 409 的冲突/失效' },
  { key: 'error', label: '演示：生成失败', hint: '流程中途失败，可重试' },
]

export function WorkspacePage() {
  const workflow = useWorkflow()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)

  const start = useCallback(async () => {
    const material = MATERIALS.find((m) => m.id === selectedId)
    if (!material) return
    setStarting(true)
    try {
      // Declare the chosen authorized material (staged input), start the durable run,
      // then bind the task workspace to the declared version. Declaration/binding are
      // best-effort against the bridge; the run's event stream drives the UI either way.
      let versionId: string | null = null
      try {
        const declared = await desktopApi.materials.declare({
          name: material.name,
          purpose: material.purpose,
          contentHash: material.hash,
        })
        versionId = declared.versionId
      } catch {
        // declaration unavailable — proceed with the run so the user is never blocked
      }
      const runId = await workflow.startWorkflow()
      if (runId && versionId) {
        try {
          await desktopApi.workspace.select({ runId, materialVersionIds: [versionId] })
        } catch {
          // workspace binding is advisory for the UI; the run remains authoritative
        }
      }
    } finally {
      setStarting(false)
    }
  }, [selectedId, workflow])

  if (workflow.active) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--ds-space-3)' }}>
        <button
          type="button"
          data-testid="workflow-back"
          onClick={workflow.reset}
          className="ds-focusable ds-ghost-btn"
          style={{ alignSelf: 'flex-start' }}
        >
          <ArrowLeft size={14} aria-hidden /> 返回工作区
        </button>
        <WorkflowPage />
      </div>
    )
  }

  return (
    <div data-testid="workspace-page" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--ds-space-5)' }}>
      <section className="ds-card" style={{ padding: 'var(--ds-space-5)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 'var(--ds-space-2)' }}>
          <FolderOpen size={20} weight="duotone" aria-hidden style={{ color: 'var(--ds-accent)' }} />
          <h2 style={{ margin: 0, fontSize: 'var(--ds-text-lg)', fontWeight: 650 }}>发起报销</h2>
        </div>
        <p style={{ marginTop: 0, color: 'var(--ds-text-secondary)', fontSize: 'var(--ds-text-base)' }}>
          选择一份你有权使用的材料，系统会据此生成报销草稿，提交财务前需经你确认。
        </p>

        <fieldset style={{ border: 'none', margin: 0, padding: 0 }}>
          <legend style={{ fontSize: 'var(--ds-text-sm)', fontWeight: 650, marginBottom: 8 }}>选择授权材料</legend>
          <div style={{ display: 'grid', gap: 'var(--ds-space-2)' }}>
            {MATERIALS.map((material) => {
              const active = material.id === selectedId
              return (
                <button
                  key={material.id}
                  type="button"
                  data-testid="material-option"
                  aria-pressed={active}
                  onClick={() => setSelectedId(material.id)}
                  className="ds-focusable"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                    border: `1px solid ${active ? 'var(--ds-accent-border)' : 'var(--ds-border)'}`,
                    background: active ? 'var(--ds-accent-soft)' : 'var(--ds-card)',
                    borderRadius: 'var(--ds-radius-md)',
                    padding: '10px 14px',
                    cursor: 'pointer',
                    textAlign: 'left',
                  }}
                >
                  <Receipt size={16} weight="duotone" aria-hidden style={{ color: 'var(--ds-accent)' }} />
                  <span style={{ flex: 1 }}>
                    <span style={{ display: 'block', fontWeight: 600, color: 'var(--ds-text)' }}>{material.name}</span>
                    <span style={{ fontSize: 'var(--ds-text-xs)', color: 'var(--ds-text-muted)' }}>{material.purpose}</span>
                  </span>
                </button>
              )
            })}
          </div>
        </fieldset>

        <button
          type="button"
          data-testid="workflow-start"
          disabled={!selectedId || starting}
          onClick={() => void start()}
          className="ds-focusable"
          style={{
            marginTop: 'var(--ds-space-4)',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 8,
            border: 'none',
            background: !selectedId || starting ? 'var(--ds-border-strong)' : 'var(--ds-accent)',
            color: '#fff',
            borderRadius: 'var(--ds-radius-md)',
            padding: '10px 18px',
            fontWeight: 600,
            cursor: !selectedId || starting ? 'not-allowed' : 'pointer',
          }}
        >
          <FileArrowUp size={16} aria-hidden /> 开始报销流程
        </button>
      </section>

      <section className="ds-card" style={{ padding: 'var(--ds-space-4)' }}>
        <div style={{ fontSize: 'var(--ds-text-sm)', fontWeight: 650, marginBottom: 4 }}>演示状态场景</div>
        <p style={{ marginTop: 0, fontSize: 'var(--ds-text-xs)', color: 'var(--ds-text-muted)' }}>
          用于复现审批中可能遇到的边界情况（无权限 / 冲突 / 失败），便于验收与演示。
        </p>
        <div style={{ display: 'flex', gap: 'var(--ds-space-2)', flexWrap: 'wrap' }}>
          {SCENARIOS.map((scenario) => (
            <button
              key={scenario.key}
              type="button"
              data-testid={`scenario-${scenario.key}`}
              onClick={() => void workflow.startWorkflow(scenario.key)}
              title={scenario.hint}
              className="ds-focusable"
              style={{
                border: '1px solid var(--ds-border)',
                background: 'var(--ds-card)',
                color: 'var(--ds-text-secondary)',
                borderRadius: 'var(--ds-radius-md)',
                padding: '7px 12px',
                cursor: 'pointer',
                fontSize: 'var(--ds-text-sm)',
              }}
            >
              {scenario.label}
            </button>
          ))}
        </div>
      </section>

      {MATERIALS.length === 0 ? (
        <EmptyState title="暂无可用材料" description="你当前没有被授权的报销材料。" />
      ) : null}
    </div>
  )
}
