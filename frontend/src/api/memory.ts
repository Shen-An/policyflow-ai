import { apiClient } from './client'

export type MemoryType =
  | 'user_preference'
  | 'long_term_event'
  | 'entity'
  | 'conversation_summary'
  | 'stm_summary'
  | 'system_note'
  | string

export type MemoryItem = {
  id: string
  ownerType: string
  ownerId: string
  memoryType: MemoryType
  content: string
  source: string
  confidence: number
  metaJson: Record<string, unknown>
  hasEmbedding: boolean
  expiresAt: string | null
  createdAt: string
  updatedAt: string
}

export type MemoryListResult = {
  items: MemoryItem[]
  total: number
  page: number
  pageSize: number
}

type MemoryItemRaw = {
  id: string
  owner_type: string
  owner_id: string
  memory_type: string
  content: string
  source: string
  confidence: number
  meta_json: Record<string, unknown>
  has_embedding: boolean
  expires_at: string | null
  created_at: string
  updated_at: string
}

type MemoryListRaw = {
  items: MemoryItemRaw[]
  total: number
  page: number
  page_size: number
}

function toMemoryItem(raw: MemoryItemRaw): MemoryItem {
  return {
    id: raw.id,
    ownerType: raw.owner_type,
    ownerId: raw.owner_id,
    memoryType: raw.memory_type,
    content: raw.content,
    source: raw.source,
    confidence: raw.confidence,
    metaJson: raw.meta_json ?? {},
    hasEmbedding: Boolean(raw.has_embedding),
    expiresAt: raw.expires_at,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
  }
}

export async function listMemories(
  page: number,
  pageSize: number,
  memoryType?: string,
  keyword?: string,
  signal?: AbortSignal,
): Promise<MemoryListResult> {
  const search = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  })
  if (memoryType) search.set('memory_type', memoryType)
  if (keyword) search.set('keyword', keyword)
  const raw = await apiClient.request<MemoryListRaw>(`/api/memory?${search.toString()}`, {
    method: 'GET',
    signal,
  })
  return {
    items: (raw.items ?? []).map(toMemoryItem),
    total: raw.total,
    page: raw.page,
    pageSize: raw.page_size,
  }
}

export async function deleteMemory(memoryId: string, signal?: AbortSignal): Promise<void> {
  await apiClient.request<void>(`/api/memory/${memoryId}`, {
    method: 'DELETE',
    signal,
  })
}
