import { ArrowSquareOut, CheckCircle, Circle, Database, Trash } from '@phosphor-icons/react'
import { desktopApi, isDesktopRuntime } from '../../services/desktop-api'

// T132 — knowledge surface. The desktop capability bridge intentionally exposes no
// knowledge-base management endpoints (upload / scan / index / delete), so rather than
// fake a live table the desktop view explains the full lifecycle status model
// (upload → scan → index → available, retrieval availability, physical delete → restore)
// and routes management to the web console. This is an honest capability boundary, not a
// dead end: every state has a clear meaning and the next step is one click away.

type Stage = { key: string; label: string; detail: string; tone: 'progress' | 'ok' | 'warn' | 'danger' }

const LIFECYCLE: Stage[] = [
  { key: 'staging', label: '上传中', detail: '文件已接收，等待校验。', tone: 'progress' },
  { key: 'scanning', label: '扫描中', detail: '正在做安全与合规扫描。', tone: 'progress' },
  { key: 'indexing', label: '索引中', detail: '正在切分、向量化并建立索引。', tone: 'progress' },
  { key: 'available', label: '可用', detail: '已可被检索并作为答复依据。', tone: 'ok' },
  { key: 'quarantined', label: '已隔离', detail: '扫描未通过，不参与检索。', tone: 'warn' },
  { key: 'superseded', label: '已被替代', detail: '存在更新版本，旧版本保留但不检索。', tone: 'warn' },
  { key: 'deleting', label: '删除中', detail: '物理删除进行中，可在保留期内恢复。', tone: 'danger' },
]

const toneColor: Record<Stage['tone'], string> = {
  progress: 'var(--ds-info)',
  ok: 'var(--ds-success)',
  warn: 'var(--ds-warning)',
  danger: 'var(--ds-danger)',
}

const WEB_CONSOLE = 'https://help.policyflow.example/knowledge-bases'

export function KnowledgePage() {
  const desktop = isDesktopRuntime()
  return (
    <div data-testid="knowledge-page" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--ds-space-5)' }}>
      <section className="ds-card" style={{ padding: 'var(--ds-space-5)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 'var(--ds-space-2)' }}>
          <Database size={20} weight="duotone" aria-hidden style={{ color: 'var(--ds-accent)' }} />
          <h2 style={{ margin: 0, fontSize: 'var(--ds-text-lg)', fontWeight: 650 }}>知识库</h2>
        </div>
        <p style={{ marginTop: 0, color: 'var(--ds-text-secondary)', fontSize: 'var(--ds-text-base)' }}>
          知识库里的制度文件会经过上传、扫描、索引后才可被检索，并作为问答的依据来源。下面是每种状态的含义。
        </p>
        {desktop ? (
          <button
            type="button"
            data-testid="knowledge-open-console"
            onClick={() => void desktopApi.system.openExternal({ url: WEB_CONSOLE })}
            className="ds-focusable"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              border: '1px solid var(--ds-accent-border)',
              background: 'var(--ds-accent-soft)',
              color: 'var(--ds-accent-text)',
              borderRadius: 'var(--ds-radius-md)',
              padding: '8px 14px',
              cursor: 'pointer',
              fontWeight: 600,
            }}
          >
            <ArrowSquareOut size={16} aria-hidden /> 在 Web 控制台管理上传与索引
          </button>
        ) : null}
      </section>

      <section className="ds-card" style={{ padding: 'var(--ds-space-4)' }}>
        <h3 style={{ marginTop: 0, fontSize: 'var(--ds-text-md)', fontWeight: 650 }}>文件状态</h3>
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 'var(--ds-space-2)' }}>
          {LIFECYCLE.map((stage) => (
            <li
              key={stage.key}
              style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '8px 0', borderBottom: '1px solid var(--ds-divider)' }}
            >
              <Circle size={12} weight="fill" aria-hidden style={{ color: toneColor[stage.tone], marginTop: 5 }} />
              <div>
                <div style={{ fontWeight: 600, color: 'var(--ds-text)' }}>{stage.label}</div>
                <div style={{ fontSize: 'var(--ds-text-sm)', color: 'var(--ds-text-secondary)' }}>{stage.detail}</div>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="ds-card" style={{ padding: 'var(--ds-space-4)' }}>
        <h3 style={{ marginTop: 0, fontSize: 'var(--ds-text-md)', fontWeight: 650 }}>检索可用性与删除</h3>
        <div style={{ display: 'grid', gap: 'var(--ds-space-2)', fontSize: 'var(--ds-text-sm)', color: 'var(--ds-text-secondary)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <CheckCircle size={15} weight="fill" aria-hidden style={{ color: 'var(--ds-success)' }} />
            只有“可用”且已激活检索版本的文件才会被检索；旧版本永不与新版本同时生效。
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Trash size={15} weight="duotone" aria-hidden style={{ color: 'var(--ds-danger)' }} />
            物理删除会移除对象与向量；在保留期内可恢复，恢复后重新索引即可再次检索。
          </div>
        </div>
      </section>
    </div>
  )
}
