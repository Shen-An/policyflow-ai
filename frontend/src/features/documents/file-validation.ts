export const SUPPORTED_DOCUMENT_EXTENSIONS = ['txt', 'md', 'docx', 'pdf'] as const
export const MAX_DOCUMENT_SIZE_BYTES = 20 * 1024 * 1024

export function validateDocumentFile(file: File): string | null {
  const extension = file.name.split('.').pop()?.toLowerCase() ?? ''
  if (!SUPPORTED_DOCUMENT_EXTENSIONS.includes(
    extension as (typeof SUPPORTED_DOCUMENT_EXTENSIONS)[number],
  )) {
    return '仅支持 TXT、Markdown、DOCX 和 PDF 文件。'
  }
  if (file.size > MAX_DOCUMENT_SIZE_BYTES) {
    return '文件不能超过 20MB。'
  }
  return null
}
