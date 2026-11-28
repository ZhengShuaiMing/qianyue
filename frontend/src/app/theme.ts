import { useEffect, useState } from 'react'

export type Theme = 'dark' | 'light'

const KEY = 'qianyue-theme'
const systemLight = () => window.matchMedia('(prefers-color-scheme: light)')

function saved(): Theme | null {
  try {
    const value = localStorage.getItem(KEY)
    return value === 'dark' || value === 'light' ? value : null
  } catch {
    return null
  }
}

/** 第一次渲染前就把主题挂到 <html> 上，免得先闪一下另一套颜色 */
export function applyInitialTheme() {
  document.documentElement.dataset.theme = saved() ?? (systemLight().matches ? 'light' : 'dark')
}

/** 主题：手动选过就用选过的，没选过就跟随系统，系统切换时跟着变 */
export function useTheme() {
  const [choice, setChoice] = useState<Theme | null>(saved)
  const [system, setSystem] = useState<Theme>(() => (systemLight().matches ? 'light' : 'dark'))
  const theme = choice ?? system

  useEffect(() => {
    const query = systemLight()
    const onChange = () => setSystem(query.matches ? 'light' : 'dark')
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])

  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])

  const toggle = () => {
    const next: Theme = theme === 'dark' ? 'light' : 'dark'
    setChoice(next)
    try { localStorage.setItem(KEY, next) } catch { /* 存不了只影响下次打开 */ }
  }
  return [theme, toggle] as const
}
