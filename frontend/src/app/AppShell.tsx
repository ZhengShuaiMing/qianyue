import { useEffect } from 'react'
import { Outlet, ScrollRestoration, useLocation } from 'react-router'
import { ToastProvider } from '../ui/ToastProvider'
import { AgentHandoff } from './AgentHandoff'
import { Atmosphere } from './Atmosphere'
import { RunsProvider } from './runs/RunsProvider'
import { PAGE_NAMES } from './nav'
import { TopBar } from './TopBar'
import { Guide } from './guide/Guide'
import './shell.css'

/** 每一页共用的外壳：背景光、顶栏、页面区域、轻提示 */
export function AppShell() {
  const { pathname } = useLocation()
  useEffect(() => {
    const name = PAGE_NAMES[`/${pathname.split('/')[1]}`]
    document.title = name ? `${name} · 迁跃` : '迁跃 Qianyue'
  }, [pathname])

  return (
    <ToastProvider>
      <RunsProvider>
      <div className="app">
        <Atmosphere />
        <TopBar />
        <main className="page">
          <Outlet />
        </main>
        <Guide />
        <AgentHandoff />
      </div>
      </RunsProvider>
      <ScrollRestoration />
    </ToastProvider>
  )
}

