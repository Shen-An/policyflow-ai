import { apiClient } from './client'

export type ModelCapability = 'chat' | 'embedding'
export type ModelEndpointSettings = {
  id: string; capability: ModelCapability; name: string; providerType: string; baseUrl: string
  authMode: 'bearer' | 'none' | 'environment'; apiStyle: 'openai_chat_completions' | 'openai_responses' | 'openai_embeddings'; apiKeyConfigured: boolean
  apiKeySource: 'database' | 'environment' | 'none'; model: string
  embeddingDimension: number | null; embeddingInputType: 'none' | 'query' | 'passage' | null; timeoutSeconds: number; enabled: boolean; updatedAt: string
}
export type ModelEndpointSettingsInput = {
  name: string; baseUrl: string; authMode: 'bearer' | 'none'; apiStyle: 'openai_chat_completions' | 'openai_responses' | 'openai_embeddings'; apiKey?: string
  clearApiKey?: boolean; model: string; embeddingDimension?: number | null; embeddingInputType?: 'none' | 'query' | 'passage' | null
  timeoutSeconds: number; enabled: boolean
}
type RawSettings = {
  id: string; capability: ModelCapability; name: string; provider_type: string; base_url: string
  auth_mode: 'bearer' | 'none' | 'environment'; api_style: 'openai_chat_completions' | 'openai_responses' | 'openai_embeddings'; api_key_configured: boolean
  api_key_source: 'database' | 'environment' | 'none'; model: string
  embedding_dimension: number | null; embedding_input_type: 'none' | 'query' | 'passage' | null; timeout_seconds: number; enabled: boolean; updated_at: string
}
function mapSettings(raw: RawSettings): ModelEndpointSettings {
  return { id: raw.id, capability: raw.capability, name: raw.name, providerType: raw.provider_type,
    baseUrl: raw.base_url, authMode: raw.auth_mode, apiStyle: raw.api_style, apiKeyConfigured: raw.api_key_configured,
    apiKeySource: raw.api_key_source, model: raw.model, embeddingDimension: raw.embedding_dimension, embeddingInputType: raw.embedding_input_type,
    timeoutSeconds: raw.timeout_seconds, enabled: raw.enabled, updatedAt: raw.updated_at }
}
export async function getModelProviderSettings(signal?: AbortSignal): Promise<{ chat: ModelEndpointSettings | null; embedding: ModelEndpointSettings | null }> {
  const raw = await apiClient.request<{ chat: RawSettings | null; embedding: RawSettings | null }>('/api/settings/model-providers', { signal })
  return { chat: raw.chat ? mapSettings(raw.chat) : null, embedding: raw.embedding ? mapSettings(raw.embedding) : null }
}
export async function updateModelProviderSettings(capability: ModelCapability, input: ModelEndpointSettingsInput): Promise<ModelEndpointSettings> {
  const raw = await apiClient.request<RawSettings>(`/api/settings/model-providers/${capability}`, {
    method: 'PUT', body: JSON.stringify({ name: input.name, base_url: input.baseUrl, auth_mode: input.authMode, api_style: input.apiStyle,
      api_key: input.apiKey || undefined, clear_api_key: input.clearApiKey ?? false, model: input.model,
      embedding_dimension: input.embeddingDimension ?? null, embedding_input_type: input.embeddingInputType ?? null, timeout_seconds: input.timeoutSeconds, enabled: input.enabled }),
  })
  return mapSettings(raw)
}
export async function listProviderModels(capability: ModelCapability): Promise<string[]> {
  const raw = await apiClient.request<{ models: string[] }>(`/api/settings/model-providers/${capability}/models`)
  return raw.models
}
export type ModelTestResult = { capability: ModelCapability; result: { status: 'passed' | 'skipped' | 'failed'; message: string; dimension: number | null; error_code?: string | null }; requestId: string | null }
export async function testModelProvider(capability: ModelCapability): Promise<ModelTestResult> {
  const raw = await apiClient.request<{ capability: ModelCapability; result: ModelTestResult['result']; request_id: string | null }>(`/api/settings/model-providers/${capability}/test`, { method: 'POST' })
  return { capability: raw.capability, result: raw.result, requestId: raw.request_id }
}
