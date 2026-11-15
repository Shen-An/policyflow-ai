import { useState } from 'react'
import { ArrowLeft, FolderOpen } from '@phosphor-icons/react'
import { useWorkflow } from './workflow-store'
import { WorkflowPage } from './workflow-page'

export function WorkspacePage() {
  const workflow = useWorkflow()
  const [versionIds, setVersionIds] = useState('')
  const [question, setQuestion] = useState('根据报销制度生成可填写的报销清单')
  if (workflow.active) return (
    <div style={{ display: 'grid', gap: 'var(--ds-space-3)' }}>
      <button type="button" data-testid="workflow-back" onClick={workflow.reset} className="ds-focusable ds-ghost-btn">
        <ArrowLeft size={14} aria-hidden /> 返回工作区
      </button>
      <WorkflowPage />
    </div>
  )
  const ids = [...new Set(versionIds.split(/[\s,，]+/u).filter(Boolean))]
  return (
    <div data-testid="workspace-page" className="ds-card" style={{ padding: 'var(--ds-space-5)' }}>
      <h2><FolderOpen size={20} aria-hidden /> 发起报销</h2>
      <p>选择已有材料版本，依据制度生成可填写草稿。材料原件只读；提交前需审批，提交目标为 mock。</p>
      <label style={{ display: 'grid', gap: 8 }}>
        材料版本 ID
        <textarea aria-label="材料版本 ID" value={versionIds} onChange={e => setVersionIds(e.target.value)}
          placeholder="从材料管理复制可用版本 ID，多个 ID 用逗号分隔" rows={3} />
      </label>
      <label style={{ display: 'grid', gap: 8, marginTop: 16 }}>
        草稿要求
        <textarea aria-label="草稿要求" value={question} onChange={e => setQuestion(e.target.value)} rows={3} />
      </label>
      <p style={{ color: 'var(--ds-text-secondary)' }}>本流程生成待填写的结构，金额和票据信息由申请人填写。</p>
      <button type="button" data-testid="workflow-start" disabled={!ids.length || !question.trim()}
        className="ds-focusable ds-ghost-btn" onClick={() => void workflow.startWorkflow(ids, question)}>
        开始报销流程
      </button>
    </div>
  )
}
