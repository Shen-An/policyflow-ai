import { createBrowserRouter } from 'react-router-dom'
import { ForbiddenPage } from './forbidden-page'
import { NotFoundPage } from './not-found-page'
import {
  AuditRouteElement,
  ChatRouteElement,
  DocumentListRouteElement,
  DraftDetailRouteElement,
  DraftListRouteElement,
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
} from './route-elements'
import { WorkspacePage } from './workspace-page'

export const router = createBrowserRouter([
  { path: '/login', element: <LoginRouteElement /> },
  {
    element: <ShellRouteElement />,
    children: [
      { index: true, element: <WorkspacePage /> },
      { path: 'forbidden', element: <ForbiddenPage /> },
      { path: 'chat', element: <ChatRouteElement /> },
      { path: 'chat/:conversationId', element: <ChatRouteElement /> },
      { path: 'drafts', element: <DraftListRouteElement /> },
      { path: 'drafts/:draftId', element: <DraftDetailRouteElement /> },
      { path: 'memory', element: <MemoryRouteElement /> },
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
