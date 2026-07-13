import { Button, Result } from 'antd'

export function ChunkLoadErrorFallback({ error }: { error?: Error }) {
  return (
    <div style={{ minHeight: '60vh', display: 'grid', placeItems: 'center', padding: 24 }}>
      <Result
        status="warning"
        title="页面资源已更新"
        subTitle={
          error?.message?.includes('Failed to fetch dynamically imported module')
            ? '检测到前端版本已刷新，旧页面缓存失效。请重新加载获取最新页面。'
            : '页面模块加载失败，请重新加载后再试。'
        }
        extra={
          <Button type="primary" onClick={() => window.location.assign(window.location.href)}>
            重新加载
          </Button>
        }
      />
    </div>
  )
}
