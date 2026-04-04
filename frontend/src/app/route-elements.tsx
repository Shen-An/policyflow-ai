import { lazy, Suspense, type ReactNode } from 'react'
import { ProtectedRoute, PublicOnlyRoute, RoleGuard } from '../auth/route-guards'
import { FullPageLoading } from '../components/feedback/full-page-loading'

const LoginPage = lazy(() => import('../features/login/login-page').then((module) => ({ default: module.LoginPage })))
const UsersPage = lazy(() => import('../features/users/users-page').then((module) => ({ default: module.UsersPage })))
const KnowledgeBaseListPage = lazy(() => import('../features/knowledge-bases/knowledge-base-list-page').then((module) => ({ default: module.KnowledgeBaseListPage })))
const KnowledgeBaseDetailPage = lazy(() => import('../features/knowledge-bases/knowledge-base-detail-page').then((module) => ({ default: module.KnowledgeBaseDetailPage })))
const KnowledgeBaseOverviewPage = lazy(() => import('../features/knowledge-bases/knowledge-base-overview-page').then((module) => ({ default: module.KnowledgeBaseOverviewPage })))
const DocumentListPage = lazy(() => import('../features/documents/document-list-page').then((module) => ({ default: module.DocumentListPage })))
const ChatPage = lazy(() => import('../features/chat/chat-page').then((module) => ({ default: module.ChatPage })))
const DraftListPage = lazy(() => import('../features/drafts/draft-list-page').then((module) => ({ default: module.DraftListPage })))
const DraftDetailPage = lazy(() => import('../features/drafts/draft-detail-page').then((module) => ({ default: module.DraftDetailPage })))
const FAQReviewPage = lazy(() => import('../features/faq-review/faq-review-page').then((module) => ({ default: module.FAQReviewPage })))
const AuditPage = lazy(() => import('../features/audit/audit-page').then((module) => ({ default: module.AuditPage })))
const EvaluationPage = lazy(() => import('../features/evaluation/evaluation-page').then((module) => ({ default: module.EvaluationPage })))
const SkillsPage = lazy(() => import('../features/skills/skills-page').then((module) => ({ default: module.SkillsPage })))
const IntegrationsPage = lazy(() => import('../features/integrations/integrations-page').then((module) => ({ default: module.IntegrationsPage })))
const ModelSettingsPage = lazy(() => import('../features/model-settings/model-settings-page').then((module) => ({ default: module.ModelSettingsPage })))
const AppShell = lazy(() => import('../components/layout/app-shell').then((module) => ({ default: module.AppShell })))

function Suspended({ children }: { children: ReactNode }) {
  return <Suspense fallback={<FullPageLoading message="正在加载页面…" />}>{children}</Suspense>
}

export function LoginRouteElement() {
  return <PublicOnlyRoute><Suspended><LoginPage /></Suspended></PublicOnlyRoute>
}

export function ShellRouteElement() {
  return <ProtectedRoute><Suspended><AppShell /></Suspended></ProtectedRoute>
}

export function UsersRouteElement() {
  return <RoleGuard required={['sys_admin']}><Suspended><UsersPage /></Suspended></RoleGuard>
}

export function KnowledgeBaseListRouteElement() {
  return <Suspended><KnowledgeBaseListPage /></Suspended>
}

export function KnowledgeBaseDetailRouteElement() {
  return <Suspended><KnowledgeBaseDetailPage /></Suspended>
}

export function KnowledgeBaseOverviewRouteElement() {
  return <Suspended><KnowledgeBaseOverviewPage /></Suspended>
}

export function DocumentListRouteElement() {
  return <Suspended><DocumentListPage /></Suspended>
}

export function ChatRouteElement() {
  return <Suspended><ChatPage /></Suspended>
}

export function DraftListRouteElement() {
  return <Suspended><DraftListPage /></Suspended>
}

export function DraftDetailRouteElement() {
  return <Suspended><DraftDetailPage /></Suspended>
}

export function FAQReviewRouteElement() {
  return <RoleGuard required={['kb_admin', 'sys_admin']}><Suspended><FAQReviewPage /></Suspended></RoleGuard>
}

export function AuditRouteElement() {
  return <RoleGuard required={['sys_admin']}><Suspended><AuditPage /></Suspended></RoleGuard>
}

export function EvaluationRouteElement() {
  return <RoleGuard required={['kb_admin', 'sys_admin']}><Suspended><EvaluationPage /></Suspended></RoleGuard>
}

export function SkillsRouteElement() {
  return <RoleGuard required={['sys_admin']}><Suspended><SkillsPage /></Suspended></RoleGuard>
}

export function IntegrationsRouteElement() {
  return <RoleGuard required={['sys_admin']}><Suspended><IntegrationsPage /></Suspended></RoleGuard>
}

export function ModelSettingsRouteElement() {
  return <RoleGuard required={['sys_admin']}><Suspended><ModelSettingsPage /></Suspended></RoleGuard>
}
