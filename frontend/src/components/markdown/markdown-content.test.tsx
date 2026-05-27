import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MarkdownContent } from './markdown-content'

describe('MarkdownContent', () => {
  it('renders headings, lists, emphasis and links safely', () => {
    render(
      <MarkdownContent
        content={[
          '## 报销模板',
          '',
          '- **金额**：填写实际发生费用',
          '- 附件：发票',
          '',
          '详见 [制度说明](https://example.com/policy)',
          '',
          '| 字段 | 说明 |',
          '| --- | --- |',
          '| 事由 | 业务背景 |',
        ].join('\n')}
      />,
    )

    expect(screen.getByRole('heading', { name: '报销模板' })).toBeVisible()
    expect(screen.getByText('金额')).toBeVisible()
    expect(screen.getByText('附件：发票')).toBeVisible()
    const link = screen.getByRole('link', { name: '制度说明' })
    expect(link).toHaveAttribute('href', 'https://example.com/policy')
    expect(link).toHaveAttribute('target', '_blank')
    expect(screen.getByText('字段')).toBeVisible()
    expect(screen.getByText('业务背景')).toBeVisible()
  })
})
