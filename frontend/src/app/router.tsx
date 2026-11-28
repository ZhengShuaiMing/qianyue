import { createBrowserRouter, createHashRouter, Navigate } from 'react-router'
import { ArtifactsPage } from '../pages/artifacts/ArtifactsPage'
import { AssistantPage } from '../pages/assistant/AssistantPage'
import { CollectPage } from '../pages/collect/CollectPage'
import { JobsPage } from '../pages/jobs/JobsPage'
import { MaterialsPage } from '../pages/materials/MaterialsPage'
import { OverviewPage } from '../pages/overview/OverviewPage'
import { SearchPage } from '../pages/search/SearchPage'
import { SettingsPage } from '../pages/settings/SettingsPage'
import { AppShell } from './AppShell'

// 在线演示版是放在静态站点子目录里的单个页面，用 # 路由，刷新任何一页都不会 404
export const router = (__DEMO__ ? createHashRouter : createBrowserRouter)([
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/overview" replace /> },
      { path: 'jobs/:jobId?', element: <JobsPage /> },
      { path: 'artifacts/:artifactId?', element: <ArtifactsPage /> },
      { path: 'collect', element: <CollectPage /> },
      { path: 'materials', element: <MaterialsPage /> },
      { path: 'overview', element: <OverviewPage /> },
      { path: 'search', element: <SearchPage /> },
      { path: 'assistant/:conversationId?', element: <AssistantPage /> },
      { path: 'settings', element: <SettingsPage /> },
      // 地址写错了就回总览
      { path: '*', element: <Navigate to="/overview" replace /> },
    ],
  },
])
