import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { openAgentHandoff, screenshotHandoff } from './agent-handoff'
import { api, apiWithStatus, sendJson } from './client'
import type { Run } from './runs'

/** recruiter 招聘方说的，me 我说的，transcript 原样粘贴的一整段聊天 */
export type Speaker = 'recruiter' | 'me' | 'transcript'
export type Communication = { id: number; speaker: Speaker; text: string; created_at: string }

const key = (jobId: number) => ['jobs', 'communications', jobId]

/** 这个岗位的沟通记录，按保存的先后排列 */
export function useCommunications(jobId: number) {
  return useQuery({ queryKey: key(jobId), queryFn: () => api<Communication[]>(`/jobs/${jobId}/communications`) })
}

/** 记一条沟通。同样的内容再记一次时，后端返回 200 和已有那条；带截图时由后端交给模型读出聊天内容，打的字接在后面。
    没有模型 Key 时带截图的返回 202 和一次识别截图运行，弹出「交给你的 Agent」 */
export function useAddCommunication(jobId: number) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async ({ images = [], ...body }: { speaker: Speaker; text: string; images?: string[] }): Promise<{ record: Communication; created: boolean } | { handedOff: Run }> => {
      const path = `/jobs/${jobId}/communications${images.length ? '/from-screenshots' : ''}`
      const { status, data } = await apiWithStatus<Communication | Run>(path, sendJson('POST', images.length ? { ...body, images } : body))
      if (status === 202) return { handedOff: data as Run }
      return { record: data as Communication, created: status === 201 }
    },
    onSuccess: (result) => {
      if ('handedOff' in result) openAgentHandoff(screenshotHandoff(result.handedOff, 'communication', client))
      return client.invalidateQueries({ queryKey: key(jobId) })
    },
  })
}

export function useDeleteCommunication(jobId: number) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api<void>(`/jobs/${jobId}/communications/${id}`, { method: 'DELETE' }),
    onSuccess: () => client.invalidateQueries({ queryKey: key(jobId) }),
  })
}
