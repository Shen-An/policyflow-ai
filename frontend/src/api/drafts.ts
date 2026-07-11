import { apiClient } from './client'

export type DraftType =
  | 'email'
  | 'checklist'
  | 'application'
  | 'faq'
  | 'help_request'
  | 'summary'

export type Draft = {
  id: string
  userId: string
  conversationId: string | null
  draftType: DraftType
  title: string
  content: string
  sourceQuestion: string
  relatedSources: Array<Record<string, unknown>>
  status: string
  createdAt: string
  updatedAt: string
}

export type DraftListResult = {
  items: Draft[]
  total: number
  page: number
  pageSize: number
}

export type CreateDraftInput = {
  conversationId?: string
  draftType: DraftType
  title: string
  content: string
  sourceQuestion: string
  relatedSources?: Array<Record<string, unknown>>
}

type DraftRaw = {
  id: string
  user_id: string
  conversation_id: string | null
  draft_type: DraftType
  title: string
  content: string
  source_question: string
  related_sources: Array<Record<string, unknown>>
  status: string
  created_at: string
  updated_at: string
}

function toDraft(raw: DraftRaw): Draft {
  return {
    id: raw.id,
    userId: raw.user_id,
    conversationId: raw.conversation_id,
    draftType: raw.draft_type,
    title: raw.title,
    content: raw.content,
    sourceQuestion: raw.source_question,
    relatedSources: raw.related_sources,
    status: raw.status,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
  }
}

export async function listDrafts(
  page: number,
  pageSize: number,
  status?: string,
  draftType?: string,
  signal?: AbortSignal,
): Promise<DraftListResult> {
  const search = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  })
  if (status) search.set('status', status)
  if (draftType) search.set('draft_type', draftType)
  const raw = await apiClient.request<{
    items: DraftRaw[]
    total: number
    page: number
    page_size: number
  }>(`/api/drafts?${search.toString()}`, { signal })
  return {
    items: raw.items.map(toDraft),
    total: raw.total,
    page: raw.page,
    pageSize: raw.page_size,
  }
}

export async function getDraft(id: string, signal?: AbortSignal): Promise<Draft> {
  return toDraft(
    await apiClient.request<DraftRaw>(`/api/drafts/${encodeURIComponent(id)}`, {
      signal,
    }),
  )
}

export async function createDraft(input: CreateDraftInput): Promise<Draft> {
  const raw = await apiClient.request<DraftRaw>('/api/drafts', {
    method: 'POST',
    body: JSON.stringify({
      conversation_id: input.conversationId ?? null,
      draft_type: input.draftType,
      title: input.title,
      content: input.content,
      source_question: input.sourceQuestion,
      related_sources: input.relatedSources ?? [],
    }),
  })
  return toDraft(raw)
}

export async function updateDraft(
  id: string,
  input: { title: string; content: string },
): Promise<Draft> {
  return toDraft(
    await apiClient.request<DraftRaw>(`/api/drafts/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify(input),
    }),
  )
}

async function changeDraftStatus(id: string, action: 'confirm' | 'discard'): Promise<Draft> {
  return toDraft(
    await apiClient.request<DraftRaw>(
      `/api/drafts/${encodeURIComponent(id)}/${action}`,
      { method: 'POST' },
    ),
  )
}

export const confirmDraft = (id: string) => changeDraftStatus(id, 'confirm')
export const discardDraft = (id: string) => changeDraftStatus(id, 'discard')

export async function exportDraft(id: string): Promise<{ exportType: string; content: string }> {
  const raw = await apiClient.request<{ export_type: string; content: string }>(
    `/api/drafts/${encodeURIComponent(id)}/export`,
    { method: 'POST' },
  )
  return { exportType: raw.export_type, content: raw.content }
}
