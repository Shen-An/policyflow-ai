export type ApiReadiness = 'implemented' | 'contract-only' | 'mock-only' | 'blocked' | 'production-off'
export type ApiCapability =
  | 'health' | 'auth' | 'users' | 'knowledgeBases' | 'documents' | 'chat'
  | 'drafts' | 'faq' | 'skills' | 'tools' | 'mcp' | 'audit' | 'eval'

export const apiReadiness = {
  health: 'implemented', auth: 'implemented', users: 'implemented',
  knowledgeBases: 'contract-only', documents: 'contract-only', chat: 'contract-only',
  drafts: 'contract-only', faq: 'contract-only', skills: 'contract-only',
  tools: 'contract-only', mcp: 'contract-only', audit: 'contract-only', eval: 'contract-only',
} as const satisfies Record<ApiCapability, ApiReadiness>

export function canCallApi(capability: ApiCapability, production = import.meta.env.PROD): boolean {
  const readiness = (apiReadiness as Record<ApiCapability, ApiReadiness>)[capability]
  return readiness === 'implemented' || (readiness === 'production-off' && !production)
}
