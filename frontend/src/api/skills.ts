import { apiClient } from './client'

export type Skill = {
  name: string
  version: string
  description: string
  enabled: boolean
  riskLevel: string
  inputSchema: Record<string, unknown>
  runnable: boolean
  implemented: boolean
  configSummary: Record<string, unknown>
}

export type SkillRunResult = {
  name: string
  output: Record<string, unknown>
  auditId: string
  requestId: string | null
}

type SkillRaw = {
  name: string
  version: string
  description: string
  enabled: boolean
  risk_level: string
  input_schema: Record<string, unknown>
  runnable: boolean
  implemented: boolean
  config_summary: Record<string, unknown>
}

function toSkill(raw: SkillRaw): Skill {
  return {
    name: raw.name,
    version: raw.version,
    description: raw.description,
    enabled: raw.enabled,
    riskLevel: raw.risk_level,
    inputSchema: raw.input_schema,
    runnable: raw.runnable,
    implemented: raw.implemented,
    configSummary: raw.config_summary,
  }
}

export async function listSkills(signal?: AbortSignal): Promise<Skill[]> {
  const raw = await apiClient.request<{ items: SkillRaw[] }>('/api/skills', { signal })
  return raw.items.map(toSkill)
}

export async function setSkillEnabled(name: string, enabled: boolean): Promise<Skill> {
  return toSkill(await apiClient.request<SkillRaw>(
    `/api/skills/${encodeURIComponent(name)}/${enabled ? 'enable' : 'disable'}`,
    { method: 'POST' },
  ))
}

export async function runSkill(
  name: string,
  input: Record<string, unknown>,
): Promise<SkillRunResult> {
  const raw = await apiClient.request<{
    name: string
    output: Record<string, unknown>
    audit_id: string
    request_id: string | null
  }>(`/api/skills/${encodeURIComponent(name)}/run`, {
    method: 'POST',
    body: JSON.stringify({ input }),
  })
  return {
    name: raw.name,
    output: raw.output,
    auditId: raw.audit_id,
    requestId: raw.request_id,
  }
}
