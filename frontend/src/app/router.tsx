import { createHashRouter } from 'react-router-dom'
import { ForbiddenPage } from './forbidden-page'
import { NotFoundPage } from './not-found-page'
import {
  ApprovalRouteElement,
  AuditRouteElement,
  ChatRouteElement,
  DocumentListRouteElement,
  DraftDetailRouteElement,
  DraftListRouteElement,
  KnowledgeRouteElement,
  MemoryRouteElement,
  EvaluationRouteElement,
  FAQReviewRouteElement,
  IntegrationsRouteElement,
  KnowledgeBaseDetailRouteElement,
  KnowledgeBaseListRouteElement,
  KnowledgeBaseOverviewRouteElement,
  LoginRouteElement,
  ModelSettingsRouteElement,
  ShellRouteElement,
  SkillsRouteElement,
  UsersRouteElement,
  WorkflowRouteElement,
  WorkspaceRouteElement,
} from './route-elements'
import { WorkspacePage } from './workspace-page'

// Stage 8: the desktop renderer is served from app://local/index.html, where a browser
// (path) router never matches and falls through to NotFound. A hash router keeps all
// routing in the URL fragment, so navigation works identically under the custom scheme
// and the production web build.
export const router = createHashRouter([
  { path: '/login', element: <LoginRouteElement /> },
  {
    element: <ShellRouteElement />,
    children: [
      { index: true, element: <WorkspacePage /> },
      { path: 'forbidden', element: <ForbiddenPage /> },
      { path: 'chat', element: <ChatRouteElement /> },
      { path: 'chat/:conversationId', element: <ChatRouteElement /> },
      { path: 'knowledge', element: <KnowledgeRouteElement /> },
      { path: 'memory', element: <MemoryRouteElement /> },
      { path: 'workspace', element: <WorkspaceRouteElement /> },
      { path: 'workflow', element: <WorkflowRouteElement /> },
      { path: 'approval', element: <ApprovalRouteElement /> },
      { path: 'drafts', element: <DraftListRouteElement /> },
      { path: 'drafts/:draftId', element: <DraftDetailRouteElement /> },
      { path: 'faq-review', element: <FAQReviewRouteElement /> },
      { path: 'evaluation', element: <EvaluationRouteElement /> },
      { path: 'admin/audit', element: <AuditRouteElement /> },
      { path: 'admin/skills', element: <SkillsRouteElement /> },
      { path: 'admin/integrations', element: <IntegrationsRouteElement /> },
      { path: 'admin/model-settings', element: <ModelSettingsRouteElement /> },
      {
        path: 'admin/users',
        element: <UsersRouteElement />,
      },
      {
        path: 'knowledge-bases',
        element: <KnowledgeBaseListRouteElement />,
      },
      {
        path: 'knowledge-bases/:kbId',
        element: <KnowledgeBaseDetailRouteElement />,
        children: [
          { index: true, element: <KnowledgeBaseOverviewRouteElement /> },
          { path: 'documents', element: <DocumentListRouteElement /> },
        ],
      },
    ],
  },
  { path: '*', element: <NotFoundPage /> },
])
