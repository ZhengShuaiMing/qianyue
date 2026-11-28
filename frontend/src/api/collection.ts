import { useMutation, useQuery } from '@tanstack/react-query'
import { api, sendJson } from './client'

export type Platform = { key: string; name: string; reads_detail_pages: boolean }
export type BrowserStatus = { available: boolean; path: string | null; message: string }

export function usePlatforms() {
  return useQuery({ queryKey: ['collection', 'platforms'], queryFn: () => api<Platform[]>('/collection/platforms'), staleTime: Infinity })
}

/** 采集页启动时检查一次：本机有没有 Chrome。点「重新检测」时再查 */
export function useBrowserStatus() {
  return useQuery({ queryKey: ['collection', 'browser'], queryFn: () => api<BrowserStatus>('/collection/browser/status'), staleTime: 30_000 })
}

/** 在专用浏览器里打开平台首页，让你自己登录或完成验证 */
export function useOpenBrowser() {
  return useMutation({ mutationFn: (platform: string) => api<{ message: string }>('/collection/browser', sendJson('POST', { platform })) })
}
