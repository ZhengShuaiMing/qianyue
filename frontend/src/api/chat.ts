import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { SNAPSHOT } from '../demo/mode'
import { ApiError, DEMO_BLOCKED, api, sendJson } from './client'

export type ConversationSummary = { id: string; title: string; updated_at: string; waiting: boolean }
/** user 你说的；assistant 助手说的；summary 较早的对话压缩成的摘要；result 专员成果开头的原文 */
export type ChatMessage = {
  role: 'user' | 'assistant' | 'summary' | 'result'
  text: string
  /** 这条话的时间；较早存下的对话没有 */
  at?: string
  tools: string[]
  /** 你随这句话发的截图（data:image/...;base64,...）；text 只是你自己打的字 */
  images?: string[]
  result?: { run_id: string; job_id: number | null; artifact_id: number; title: string; truncated: boolean }
}
export type PendingAction = { id: string; tool: string; description: string }
export type Conversation = { id: string; messages: ChatMessage[]; waiting: PendingAction[] }

export function useConversations() {
  return useQuery({ queryKey: ['chat', 'list'], queryFn: () => api<ConversationSummary[]>('/chat') })
}

export function useConversation(id: string | null) {
  return useQuery({ queryKey: ['chat', 'detail', id], queryFn: () => api<Conversation>(`/chat/${id}`), enabled: id !== null })
}

export function useRenameConversation() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => api<ConversationSummary>(`/chat/${id}`, sendJson('PATCH', { title })),
    onSuccess: () => client.invalidateQueries({ queryKey: ['chat', 'list'] }),
  })
}

/** 删除对话和它的全部历史。对话里发起的评估等运行和成果不受影响 */
export function useDeleteConversation() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api<void>(`/chat/${id}`, { method: 'DELETE' }),
    onSuccess: (_, id) => {
      client.removeQueries({ queryKey: ['chat', 'detail', id] })
      void client.invalidateQueries({ queryKey: ['chat', 'list'] })
    },
  })
}

/** 先把截图存进迁跃，返回编号（保留 7 天）。没有模型 Key 时助手的截图这样交给用户的 Agent 去看 */
export function uploadScreenshots(images: string[]) {
  return api<{ id: string; count: number }>('/uploads', sendJson('POST', { images }))
}

/** 发一个 POST，按 SSE 格式逐条读回事件（浏览器自带的 EventSource 只能 GET） */
export async function streamEvents(path: string, body: unknown, onEvent: (name: string, data: Record<string, unknown>) => void) {
  if (SNAPSHOT) throw new ApiError(403, DEMO_BLOCKED)
  const res = await fetch(`/api${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!res.ok || !res.body) {
    const detail = await res.json().catch(() => null)
    const message = typeof detail?.detail === 'string' ? detail.detail
      : Array.isArray(detail?.detail) ? String(detail.detail[0]?.msg ?? '').replace(/^Value error, /, '')
        : res.status === 502 ? '连不上后端，请先启动后端服务' : `请求失败（${res.status}）`
    throw new ApiError(res.status, message)
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value
    let end
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, end)
      buffer = buffer.slice(end + 2)
      const name = block.match(/^event: (.*)$/m)?.[1]
      const data = block.match(/^data: (.*)$/m)?.[1]
      if (name && data) onEvent(name, JSON.parse(data))
    }
  }
}
