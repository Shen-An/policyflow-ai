import { apiClient } from './client'

export type FAQDraft = {
  id: string
  knowledgeBaseId: string
  knowledgeBaseName: string
  sourceDocumentId: string | null
  sourceDocumentTitle: string | null
  sourceConversationId: string | null
  question: string
  answer: string
  status: string
  generatedBy: string
  reviewerId: string | null
  reviewNote: string | null
  createdAt: string
  updatedAt: string
}

type FAQRaw = {
  id: string
  knowledge_base_id: string
  knowledge_base_name: string
  source_document_id: string | null
  source_document_title: string | null
  source_conversation_id: string | null
  question: string
  answer: string
  status: string
  generated_by: string
  reviewer_id: string | null
  review_note: string | null
  created_at: string
  updated_at: string
}

function toFAQ(raw: FAQRaw): FAQDraft {
  return {
    id: raw.id,
    knowledgeBaseId: raw.knowledge_base_id,
    knowledgeBaseName: raw.knowledge_base_name,
    sourceDocumentId: raw.source_document_id,
    sourceDocumentTitle: raw.source_document_title,
    sourceConversationId: raw.source_conversation_id,
    question: raw.question,
    answer: raw.answer,
    status: raw.status,
    generatedBy: raw.generated_by,
    reviewerId: raw.reviewer_id,
    reviewNote: raw.review_note,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
  }
}

export async function listFAQDrafts(
  knowledgeBaseId?: string,
  status?: string,
  signal?: AbortSignal,
): Promise<FAQDraft[]> {
  const search = new URLSearchParams()
  if (knowledgeBaseId) search.set('knowledge_base_id', knowledgeBaseId)
  if (status) search.set('status', status)
  const suffix = search.size ? `?${search.toString()}` : ''
  const raw = await apiClient.request<{ items: FAQRaw[] }>(
    `/api/faq-drafts${suffix}`,
    { signal },
  )
  return raw.items.map(toFAQ)
}

export async function approveFAQ(id: string): Promise<{
  faq: FAQDraft
  documentId: string
  indexJobId: string
}> {
  const raw = await apiClient.request<{
    faq_draft: FAQRaw
    document_id: string
    index_job_id: string
  }>(`/api/faq-drafts/${encodeURIComponent(id)}/approve`, { method: 'POST' })
  return {
    faq: toFAQ(raw.faq_draft),
    documentId: raw.document_id,
    indexJobId: raw.index_job_id,
  }
}

export async function rejectFAQ(id: string, reason: string): Promise<FAQDraft> {
  return toFAQ(
    await apiClient.request<FAQRaw>(
      `/api/faq-drafts/${encodeURIComponent(id)}/reject`,
      { method: 'POST', body: JSON.stringify({ reason }) },
    ),
  )
}
