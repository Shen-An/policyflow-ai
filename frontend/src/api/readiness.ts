export type ApiReadiness = 'implemented' | 'contract-only' | 'mock-only' | 'blocked' | 'production-off'
export type ApiCapability =
  | 'health' | 'auth' | 'users' | 'knowledgeBases' | 'documents' | 'chat'
  | 'feedback' | 'drafts' | 'memory' | 'faq' | 'skills' | 'tools' | 'mcp' | 'audit' | 'eval' | 'modelSettings'

export const apiReadiness = {
  health: 'implemented', auth: 'implemented', users: 'implemented',
  knowledgeBases: 'implemented', documents: 'implemented', chat: 'implemented',
  feedback: 'implemented', drafts: 'implemented', memory: 'implemented', faq: 'implemented', skills: 'implemented',
  tools: 'implemented', mcp: 'implemented', audit: 'implemented', eval: 'implemented',
  modelSettings: 'implemented',
} as const satisfies Record<ApiCapability, ApiReadiness>

export function canCallApi(capability: ApiCapability, production = import.meta.env.PROD): boolean {
  const readiness = (apiReadiness as Record<ApiCapability, ApiReadiness>)[capability]
  return readiness === 'implemented' || (readiness === 'production-off' && !production)
}
