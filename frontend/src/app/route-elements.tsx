import { Component, type ReactNode, lazy, Suspense, type ComponentType } from 'react'
import { ProtectedRoute, PublicOnlyRoute, RoleGuard } from '../auth/route-guards'
import { FullPageLoading } from '../components/feedback/full-page-loading'
import { ChunkLoadErrorFallback } from '../components/feedback/chunk-load-error'

const CHUNK_RELOAD_KEY = 'policyflow:chunk-reload'

function isChunkLoadError(error: unknown): boolean {
  if (!(error instanceof Error)) return false
  const message = error.message || ''
  const name = error.name || ''
  return (
    name === 'ChunkLoadError' ||
    message.includes('Failed to fetch dynamically imported module') ||
    message.includes('Importing a module script failed') ||
    message.includes('error loading dynamically imported module') ||
    message.includes('Loading chunk') ||
    message.includes('Loading CSS chunk')
  )
}

function lazyWithRetry<T extends ComponentType<unknown>>(
  factory: () => Promise<{ default: T }>,
): React.LazyExoticComponent<T> {
  return lazy(async () => {
    try {
      const module = await factory()
      window.sessionStorage.removeItem(CHUNK_RELOAD_KEY)
      return module
    } catch (error) {
      if (isChunkLoadError(error) && typeof window !== 'undefined') {
        const alreadyReloaded = window.sessionStorage.getItem(CHUNK_RELOAD_KEY) === '1'
        if (!alreadyReloaded) {
          window.sessionStorage.setItem(CHUNK_RELOAD_KEY, '1')
          window.location.reload()
          // Keep suspense pending while the page reloads.
          return new Promise(() => undefined)
        }
      }
      throw error
    }
  })
}

class RouteErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  render() {
    if (this.state.error) {
      if (isChunkLoadError(this.state.error)) {
        return <ChunkLoadErrorFallback error={this.state.error} />
      }
      throw this.state.error
    }
    return this.props.children
  }
}

const LoginPage = lazyWithRetry(() =>
  import('../features/login/login-page').then((module) => ({ default: module.LoginPage })),
)
const UsersPage = lazyWithRetry(() =>
  import('../features/users/users-page').then((module) => ({ default: module.UsersPage })),
)
const KnowledgeBaseListPage = lazyWithRetry(() =>
  import('../features/knowledge-bases/knowledge-base-list-page').then((module) => ({
    default: module.KnowledgeBaseListPage,
  })),
)
const KnowledgeBaseDetailPage = lazyWithRetry(() =>
  import('../features/knowledge-bases/knowledge-base-detail-page').then((module) => ({
    default: module.KnowledgeBaseDetailPage,
  })),
)
const KnowledgeBaseOverviewPage = lazyWithRetry(() =>
  import('../features/knowledge-bases/knowledge-base-overview-page').then((module) => ({
    default: module.KnowledgeBaseOverviewPage,
  })),
)
const DocumentListPage = lazyWithRetry(() =>
  import('../features/documents/document-list-page').then((module) => ({
    default: module.DocumentListPage,
  })),
)
const ChatPage = lazyWithRetry(() =>
  import('../features/chat/chat-page').then((module) => ({ default: module.ChatPage })),
)
const DraftListPage = lazyWithRetry(() =>
  import('../features/drafts/draft-list-page').then((module) => ({ default: module.DraftListPage })),
)
const DraftDetailPage = lazyWithRetry(() =>
  import('../features/drafts/draft-detail-page').then((module) => ({
    default: module.DraftDetailPage,
  })),
)
const FAQReviewPage = lazyWithRetry(() =>
  import('../features/faq-review/faq-review-page').then((module) => ({
    default: module.FAQReviewPage,
  })),
)
const AuditPage = lazyWithRetry(() =>
  import('../features/audit/audit-page').then((module) => ({ default: module.AuditPage })),
)
const EvaluationPage = lazyWithRetry(() =>
  import('../features/evaluation/evaluation-page').then((module) => ({
    default: module.EvaluationPage,
  })),
)
const SkillsPage = lazyWithRetry(() =>
  import('../features/skills/skills-page').then((module) => ({ default: module.SkillsPage })),
)
const IntegrationsPage = lazyWithRetry(() =>
  import('../features/integrations/integrations-page').then((module) => ({
    default: module.IntegrationsPage,
  })),
)
const ModelSettingsPage = lazyWithRetry(() =>
  import('../features/model-settings/model-settings-page').then((module) => ({
    default: module.ModelSettingsPage,
  })),
)
const AppShell = lazyWithRetry(() =>
  import('../components/layout/app-shell').then((module) => ({ default: module.AppShell })),
)

function Suspended({ children }: { children: ReactNode }) {
  return (
    <RouteErrorBoundary>
      <Suspense fallback={<FullPageLoading message="正在加载页面…" />}>{children}</Suspense>
    </RouteErrorBoundary>
  )
}

export function LoginRouteElement() {
  return (
    <PublicOnlyRoute>
      <Suspended>
        <LoginPage />
      </Suspended>
    </PublicOnlyRoute>
  )
}

export function ShellRouteElement() {
  return (
    <ProtectedRoute>
      <Suspended>
        <AppShell />
      </Suspended>
    </ProtectedRoute>
  )
}

export function UsersRouteElement() {
  return (
    <RoleGuard required={['sys_admin']}>
      <Suspended>
        <UsersPage />
      </Suspended>
    </RoleGuard>
  )
}

export function KnowledgeBaseListRouteElement() {
  return (
    <Suspended>
      <KnowledgeBaseListPage />
    </Suspended>
  )
}

export function KnowledgeBaseDetailRouteElement() {
  return (
    <Suspended>
      <KnowledgeBaseDetailPage />
    </Suspended>
  )
}

export function KnowledgeBaseOverviewRouteElement() {
  return (
    <Suspended>
      <KnowledgeBaseOverviewPage />
    </Suspended>
  )
}

export function DocumentListRouteElement() {
  return (
    <Suspended>
      <DocumentListPage />
    </Suspended>
  )
}

export function ChatRouteElement() {
  return (
    <Suspended>
      <ChatPage />
    </Suspended>
  )
}

export function DraftListRouteElement() {
  return (
    <Suspended>
      <DraftListPage />
    </Suspended>
  )
}

export function DraftDetailRouteElement() {
  return (
    <Suspended>
      <DraftDetailPage />
    </Suspended>
  )
}

export function FAQReviewRouteElement() {
  return (
    <RoleGuard required={['kb_admin', 'sys_admin']}>
      <Suspended>
        <FAQReviewPage />
      </Suspended>
    </RoleGuard>
  )
}

export function AuditRouteElement() {
  return (
    <RoleGuard required={['sys_admin']}>
      <Suspended>
        <AuditPage />
      </Suspended>
    </RoleGuard>
  )
}

export function EvaluationRouteElement() {
  return (
    <RoleGuard required={['kb_admin', 'sys_admin']}>
      <Suspended>
        <EvaluationPage />
      </Suspended>
    </RoleGuard>
  )
}

export function SkillsRouteElement() {
  return (
    <RoleGuard required={['sys_admin']}>
      <Suspended>
        <SkillsPage />
      </Suspended>
    </RoleGuard>
  )
}

export function IntegrationsRouteElement() {
  return (
    <RoleGuard required={['sys_admin']}>
      <Suspended>
        <IntegrationsPage />
      </Suspended>
    </RoleGuard>
  )
}

export function ModelSettingsRouteElement() {
  return (
    <RoleGuard required={['sys_admin']}>
      <Suspended>
        <ModelSettingsPage />
      </Suspended>
    </RoleGuard>
  )
}
