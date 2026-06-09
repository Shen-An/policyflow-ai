import { apiClient } from './client'

export type QueryMode = 'naive' | 'local' | 'global' | 'hybrid' | 'mix'
export type ResourcePermission = 'read' | 'write' | 'admin'

export type KnowledgeBase = {
  id: string
  name: string
  code: string
  departmentId: string
  description: string
  ragWorkspace: string
  defaultQueryMode: QueryMode
  status: string
  permission: ResourcePermission
  documentCount: number
}

export type DepartmentOption = { id: string; code: string; name: string }
export type CreateKnowledgeBaseInput = {
  name: string
  code: string
  departmentId: string
  description: string
  defaultQueryMode: QueryMode
}

export type UpdateKnowledgeBaseInput = {
  name?: string
  description?: string
  defaultQueryMode?: QueryMode
  status?: 'active' | 'disabled'
}

export type UpdateDocumentInput = {
  title?: string
}

export type KnowledgeDocument = {
  id: string
  title: string
  fileType: string
  indexStatus: string
  sourceVersion: number
  createdAt: string
}

export type DocumentListResult = {
  items: KnowledgeDocument[]
  total: number
  page: number
  pageSize: number
}

export type DocumentStatus = {
  documentId: string
  indexStatus: string
  indexError: string | null
  latestJob: {
    id: string
    status: string
    startedAt: string | null
    finishedAt: string | null
  } | null
}

export type DocumentDetail = {
  id: string
  knowledgeBaseId: string
  title: string
  fileType: string
  indexStatus: string
  indexError: string | null
  sourceVersion: number
  contentText: string
  contentPreview: string
  contentLength: number
  createdAt: string
  updatedAt: string
}

export type DocumentUploadResult = {
  documentId: string
  title: string
  fileType: string
  indexStatus: string
  indexJobId: string
}

type KnowledgeBaseRaw = {
  id: string
  name: string
  code: string
  department_id: string
  description: string
  rag_workspace: string
  default_query_mode: string
  status: string
  permission: ResourcePermission
  document_count: number
}

type DocumentRaw = {
  id: string
  title: string
  file_type: string
  index_status: string
  source_version: number
  created_at: string
}

function toKnowledgeBase(raw: KnowledgeBaseRaw): KnowledgeBase {
  return {
    id: raw.id,
    name: raw.name,
    code: raw.code,
    departmentId: raw.department_id,
    description: raw.description,
    ragWorkspace: raw.rag_workspace,
    defaultQueryMode: raw.default_query_mode as QueryMode,
    status: raw.status,
    permission: raw.permission,
    documentCount: raw.document_count,
  }
}

function toDocument(raw: DocumentRaw): KnowledgeDocument {
  return {
    id: raw.id,
    title: raw.title,
    fileType: raw.file_type,
    indexStatus: raw.index_status,
    sourceVersion: raw.source_version,
    createdAt: raw.created_at,
  }
}

export async function listKnowledgeBases(signal?: AbortSignal): Promise<KnowledgeBase[]> {
  const raw = await apiClient.request<{ items: KnowledgeBaseRaw[]; total: number }>(
    '/api/knowledge-bases',
    { signal },
  )
  return raw.items.map(toKnowledgeBase)
}

export async function getKnowledgeBase(
  id: string,
  signal?: AbortSignal,
): Promise<KnowledgeBase> {
  const raw = await apiClient.request<KnowledgeBaseRaw>(
    `/api/knowledge-bases/${encodeURIComponent(id)}`,
    { signal },
  )
  return toKnowledgeBase(raw)
}

export async function getCreateOptions(signal?: AbortSignal): Promise<DepartmentOption[]> {
  const raw = await apiClient.request<{ departments: DepartmentOption[] }>(
    '/api/knowledge-bases/create-options',
    { signal },
  )
  return raw.departments
}

export async function createKnowledgeBase(
  input: CreateKnowledgeBaseInput,
): Promise<KnowledgeBase> {
  const raw = await apiClient.request<KnowledgeBaseRaw>('/api/knowledge-bases', {
    method: 'POST',
    body: JSON.stringify({
      name: input.name,
      code: input.code,
      department_id: input.departmentId,
      description: input.description,
      default_query_mode: input.defaultQueryMode,
    }),
  })
  return toKnowledgeBase(raw)
}

export async function updateKnowledgeBase(
  id: string,
  input: UpdateKnowledgeBaseInput,
): Promise<KnowledgeBase> {
  const body: Record<string, unknown> = {}
  if (input.name !== undefined) body.name = input.name
  if (input.description !== undefined) body.description = input.description
  if (input.defaultQueryMode !== undefined) body.default_query_mode = input.defaultQueryMode
  if (input.status !== undefined) body.status = input.status
  const raw = await apiClient.request<KnowledgeBaseRaw>(
    `/api/knowledge-bases/${encodeURIComponent(id)}`,
    {
      method: 'PATCH',
      body: JSON.stringify(body),
    },
  )
  return toKnowledgeBase(raw)
}

export async function deleteKnowledgeBase(
  id: string,
): Promise<{ knowledgeBaseId: string; status: string; deleted: boolean; documentsDeleted: number }> {
  const raw = await apiClient.request<{
    knowledge_base_id: string
    status: string
    deleted: boolean
    documents_deleted: number
  }>(`/api/knowledge-bases/${encodeURIComponent(id)}`, { method: 'DELETE' })
  return {
    knowledgeBaseId: raw.knowledge_base_id,
    status: raw.status,
    deleted: raw.deleted,
    documentsDeleted: raw.documents_deleted,
  }
}

export async function listDocuments(
  knowledgeBaseId: string,
  page: number,
  pageSize: number,
  signal?: AbortSignal,
): Promise<DocumentListResult> {
  const search = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  const raw = await apiClient.request<{
    items: DocumentRaw[]
    total: number
    page: number
    page_size: number
  }>(
    `/api/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents?${search.toString()}`,
    { signal },
  )
  return {
    items: raw.items.map(toDocument),
    total: raw.total,
    page: raw.page,
    pageSize: raw.page_size,
  }
}

export async function uploadDocument(
  knowledgeBaseId: string,
  file: File,
  title?: string,
): Promise<DocumentUploadResult> {
  const form = new FormData()
  form.append('file', file)
  if (title?.trim()) form.append('title', title.trim())
  const raw = await apiClient.request<{
    document_id: string
    title: string
    file_type: string
    index_status: string
    index_job_id: string
  }>(`/api/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents`, {
    method: 'POST',
    body: form,
  })
  return {
    documentId: raw.document_id,
    title: raw.title,
    fileType: raw.file_type,
    indexStatus: raw.index_status,
    indexJobId: raw.index_job_id,
  }
}

export async function getDocumentStatus(
  documentId: string,
  signal?: AbortSignal,
): Promise<DocumentStatus> {
  const raw = await apiClient.request<{
    document_id: string
    index_status: string
    index_error: string | null
    latest_job: {
      id: string
      status: string
      started_at: string | null
      finished_at: string | null
    } | null
  }>(`/api/documents/${encodeURIComponent(documentId)}/status`, { signal })
  return {
    documentId: raw.document_id,
    indexStatus: raw.index_status,
    indexError: raw.index_error,
    latestJob: raw.latest_job
      ? {
          id: raw.latest_job.id,
          status: raw.latest_job.status,
          startedAt: raw.latest_job.started_at,
          finishedAt: raw.latest_job.finished_at,
        }
      : null,
  }
}

export async function getDocumentDetail(
  documentId: string,
  signal?: AbortSignal,
): Promise<DocumentDetail> {
  const raw = await apiClient.request<{
    id: string
    knowledge_base_id: string
    title: string
    file_type: string
    index_status: string
    index_error: string | null
    source_version: number
    content_text: string
    content_preview: string
    content_length: number
    created_at: string
    updated_at: string
  }>(`/api/documents/${encodeURIComponent(documentId)}`, { signal })
  return {
    id: raw.id,
    knowledgeBaseId: raw.knowledge_base_id,
    title: raw.title,
    fileType: raw.file_type,
    indexStatus: raw.index_status,
    indexError: raw.index_error,
    sourceVersion: raw.source_version,
    contentText: raw.content_text,
    contentPreview: raw.content_preview,
    contentLength: raw.content_length,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
  }
}

export async function reindexDocument(
  documentId: string,
): Promise<{ jobId: string; status: string }> {
  const raw = await apiClient.request<{ job_id: string; status: string }>(
    `/api/documents/${encodeURIComponent(documentId)}/index`,
    { method: 'POST' },
  )
  return { jobId: raw.job_id, status: raw.status }
}

export async function updateDocument(
  documentId: string,
  input: UpdateDocumentInput,
): Promise<DocumentDetail> {
  const body: Record<string, unknown> = {}
  if (input.title !== undefined) body.title = input.title
  const raw = await apiClient.request<{
    id: string
    knowledge_base_id: string
    title: string
    file_type: string
    index_status: string
    index_error: string | null
    source_version: number
    content_text: string
    content_preview: string
    content_length: number
    created_at: string
    updated_at: string
  }>(`/api/documents/${encodeURIComponent(documentId)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
  return {
    id: raw.id,
    knowledgeBaseId: raw.knowledge_base_id,
    title: raw.title,
    fileType: raw.file_type,
    indexStatus: raw.index_status,
    indexError: raw.index_error,
    sourceVersion: raw.source_version,
    contentText: raw.content_text,
    contentPreview: raw.content_preview,
    contentLength: raw.content_length,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
  }
}

export async function deleteDocument(
  documentId: string,
): Promise<{ documentId: string; indexStatus: string; deleted: boolean }> {
  const raw = await apiClient.request<{
    document_id: string
    index_status: string
    deleted: boolean
  }>(`/api/documents/${encodeURIComponent(documentId)}`, { method: 'DELETE' })
  return {
    documentId: raw.document_id,
    indexStatus: raw.index_status,
    deleted: raw.deleted,
  }
}
