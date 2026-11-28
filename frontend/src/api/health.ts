import { useQuery } from '@tanstack/react-query'
import { api } from './client'

export type Health = { status: 'ok'; llm_configured: boolean }

/** 后端是否在运行、模型 Key 是否填了。每 10 秒看一次，后端重启后顶栏能自己恢复 */
export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: () => api<Health>('/health'),
    refetchInterval: 10_000,
  })
}
