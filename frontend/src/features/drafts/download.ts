export function safeDraftFilename(title: string): string {
  const sanitized = Array.from(title)
    .map((character) =>
      character.charCodeAt(0) < 32 || '<>:"/\\|?*'.includes(character)
        ? '-'
        : character,
    )
    .join('')
  const cleaned = sanitized
    .replace(/\s+/gu, ' ')
    .trim()
    .replace(/[. ]+$/gu, '')
  return `${cleaned || 'policyflow-draft'}.md`
}

export function downloadMarkdown(title: string, content: string): void {
  const url = URL.createObjectURL(new Blob([content], { type: 'text/markdown;charset=utf-8' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = safeDraftFilename(title)
  anchor.click()
  URL.revokeObjectURL(url)
}
