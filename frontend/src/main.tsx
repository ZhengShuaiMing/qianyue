import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from 'react-router/dom'
import '@fontsource/noto-serif-sc/700.css'
import '@fontsource-variable/noto-sans-sc/wght.css'
import './styles/tokens.css'
import './styles/base.css'
import './styles/scroll-fade.css'
import { router } from './app/router'
import { applyInitialTheme } from './app/theme'

applyInitialTheme()

// 后端在本机，出错基本是真的错（没启动、参数不对），不自动重试，直接把原因给用户看
const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
