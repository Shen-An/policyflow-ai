import { HttpResponse, http } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../mocks/server'
import { getModelProviderSettings, listProviderModels, testModelProvider, updateModelProviderSettings } from './model-settings'
const chat = { id: 'chat-1', capability: 'chat', name: 'company-a', provider_type: 'openai_compatible', base_url: 'https://chat.example.com/v1', auth_mode: 'bearer', api_style: 'openai_responses', api_key_configured: true, api_key_source: 'database', model: 'chat-a', embedding_dimension: null, embedding_input_type: null, timeout_seconds: 30, enabled: true, updated_at: '2026-07-11T00:00:00Z' }
const embedding = { ...chat, id: 'embedding-1', capability: 'embedding', api_style: 'openai_embeddings', name: 'company-b', base_url: 'https://embedding.example.com/v1', model: 'embed-b', embedding_dimension: 1536, embedding_input_type: 'query' }
describe('independent model settings API', () => {
  it('maps separate Chat and Embedding providers', async () => {
    server.use(http.get('*/api/settings/model-providers', () => HttpResponse.json({ chat, embedding })), http.put('*/api/settings/model-providers/embedding', async ({ request }) => { const body = await request.json() as Record<string, unknown>; expect(body).toMatchObject({ base_url: 'https://new-embedding.example.com/v1', model: 'embed-new', embedding_dimension: 3072 }); return HttpResponse.json({ ...embedding, ...body }) }))
    await expect(getModelProviderSettings()).resolves.toMatchObject({ chat: { name: 'company-a', model: 'chat-a' }, embedding: { name: 'company-b', embeddingDimension: 1536 } })
    await expect(updateModelProviderSettings('embedding', { name: 'company-c', baseUrl: 'https://new-embedding.example.com/v1', authMode: 'bearer', apiStyle: 'openai_embeddings', apiKey: 'secret', model: 'embed-new', embeddingDimension: 3072, embeddingInputType: 'query', timeoutSeconds: 90, enabled: true })).resolves.toMatchObject({ model: 'embed-new', embeddingDimension: 3072 })
  })
  it('uses capability-specific catalog and test endpoints', async () => {
    server.use(http.get('*/api/settings/model-providers/chat/models', () => HttpResponse.json({ capability: 'chat', models: ['chat-a'] })), http.post('*/api/settings/model-providers/embedding/test', () => HttpResponse.json({ capability: 'embedding', result: { status: 'passed', message: 'vector', dimension: 1536 }, request_id: 'req-1' })))
    await expect(listProviderModels('chat')).resolves.toEqual(['chat-a'])
    await expect(testModelProvider('embedding')).resolves.toMatchObject({ capability: 'embedding', result: { dimension: 1536 }, requestId: 'req-1' })
  })
})
