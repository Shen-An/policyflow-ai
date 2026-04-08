import { describe, expect, it } from 'vitest'
import {
  MAX_DOCUMENT_SIZE_BYTES,
  validateDocumentFile,
} from './file-validation'

describe('validateDocumentFile', () => {
  it.each(['txt', 'md', 'docx', 'pdf'])('accepts .%s files case-insensitively', (extension) => {
    const file = new File(['content'], `policy.${extension.toUpperCase()}`)
    expect(validateDocumentFile(file)).toBeNull()
  })

  it('rejects unsupported file types', () => {
    expect(validateDocumentFile(new File(['content'], 'policy.exe'))).toBe(
      '仅支持 TXT、Markdown、DOCX 和 PDF 文件。',
    )
  })

  it('rejects files larger than 20MB', () => {
    const file = new File(
      [new Uint8Array(MAX_DOCUMENT_SIZE_BYTES + 1)],
      'large.pdf',
    )
    expect(validateDocumentFile(file)).toBe('文件不能超过 20MB。')
  })
})
