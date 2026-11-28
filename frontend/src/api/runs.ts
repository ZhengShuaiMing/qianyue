import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { needsAgent, openAgentHandoff, taskHandoff } from './agent-handoff'
import { api, sendJson } from './client'

export type RunKind =
  | 'evaluation' | 'tailoring' | 'company_research' | 'communication'
  | 'interview_prep' | 'mock_interview' | 'collection' | 'profile' | 'screenshot'
export type RunStatus = 'running' | 'waiting_user' | 'succeeded' | 'failed' | 'cancelled'

export type Run = {
  id: string
  kind: RunKind
  job_ids: number[]
  status: RunStatus
  /** 现在在做什么：单个岗位是节点名，批量是「已完成 2/5」 */
  step: string
  error: string | null
  created_at: string
  updated_at: string
  finished_at: string | null
  /** 一共几项：采集按搜索算，能力画像算一项，其余按岗位算 */
  total: number
  /** 正在进行时已经有结论的项数；不在进行时为 null */
  done: number | null
  /** 没有模型 Key、由用户的 Agent 代答时：waiting 等 Agent 接手，working Agent 在做 */
  agent: 'waiting' | 'working' | null
}

/** 一项的结论。done 生成了成果，reused 复用已有成果，blocked 前置检查没过，failed 模型输出始终不合格 */
export type RunResult = {
  job_id: number | null
  status: string
  artifact_id: number | null
  reason: string
  /** 采集时：这一项是哪个搜索，新增了哪些岗位 */
  search?: string | null
  saved?: number[]
}
export type CollectionScope = {
  searches: { platform: string; query: string; city: string; count: number }[]
  filters: Record<string, unknown>
  evaluate: boolean
}
export type RunDetail = Run & {
  conditions: string
  force: boolean
  results: RunResult[]
  pending: Record<string, unknown> | null
  collection: CollectionScope | null
}

export const RUN_KINDS: Record<RunKind, string> = {
  evaluation: '岗位评估',
  tailoring: '定制简历',
  company_research: '公司研究',
  communication: '沟通建议',
  interview_prep: '面试准备',
  mock_interview: '模拟面试',
  collection: '岗位采集',
  profile: '能力画像',
  screenshot: '识别截图',
}

/** 最近的运行。有运行在进行时 2 秒看一次；都停了就 10 秒看一次，助手或采集在别处发起的也能看到 */
export function useRuns() {
  return useQuery({
    queryKey: ['runs', 'list'],
    queryFn: () => api<{ items: Run[]; total: number }>('/runs?limit=50'),
    refetchInterval: (query) => (query.state.data?.items.some((r) => r.status === 'running') ? 2000 : 10_000),
  })
}

export function useRun(id: string | null) {
  return useQuery({ queryKey: ['runs', 'detail', id], queryFn: () => api<RunDetail>(`/runs/${id}`), enabled: id !== null })
}

/** 采集的范围：平台 × 关键词 × 城市的每种组合是一个搜索。per_platform 按平台给数量，没写的用 per_search */
export type CollectionRequest = {
  platforms: string[]
  queries: string[]
  cities: string[]
  per_search: number
  per_platform: Record<string, number>
  min_salary: number | null
  years: number | null
  exclude: string[]
  relevant_only: boolean
  evaluate: boolean
  list_only: boolean
}
export type StartRun = { kind: RunKind; job_ids: number[]; conditions?: string; force?: boolean; collection?: CollectionRequest }

/** 发起一次运行。同样的任务还在进行时，后端返回那一次，不重复发起 */
export function useStartRun() {
  const client = useQueryClient()
  const mutation = useMutation({
    mutationFn: (body: StartRun) => api<Run>('/runs', sendJson('POST', body)),
    onSuccess: () => client.invalidateQueries({ queryKey: ['runs'] }),
  })
  // 没有模型 Key 时不去后端碰壁：弹出「交给你的 Agent」，给出一句可以复制过去的话
  const mutate: typeof mutation.mutate = (body, options) => {
    if (needsAgent(client, body)) openAgentHandoff(taskHandoff(client, body))
    else mutation.mutate(body, options)
  }
  return { ...mutation, mutate }
}

/** 取消、继续：都是对一个已有运行的操作 */
export function useRunAction(action: 'cancel' | 'resume') {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api<Run>(`/runs/${id}/${action}`, { method: 'POST' }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['runs'] }),
  })
}

/** 提交对「等你确认」的答复。定制简历时是接受的修改编号，空列表表示一条都不要 */
export function useRespondRun() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, accepted, overrides = {} }: { id: string; accepted: number[]; overrides?: Overrides }) =>
      api<Run>(`/runs/${id}/respond`, sendJson('POST', { accepted, overrides })),
    onSuccess: () => client.invalidateQueries({ queryKey: ['runs'] }),
  })
}

/** 你自己改过的新内容：修改编号 → 你写的文字。只放勾选了的修改 */
export type Overrides = Record<number, string>

/** 定制简历确认前的预览：只应用勾选的这几条修改（带上你改过的）后的简历。不保存任何东西 */
export function useTailoringPreview(runId: string, accepted: number[], overrides: Overrides) {
  const key = [...accepted].sort((a, b) => a - b)
  return useQuery({
    queryKey: ['runs', 'preview', runId, key, overrides],
    queryFn: () => api<{ preview_md: string }>(`/runs/${runId}/preview`, sendJson('POST', { accepted: key, overrides })),
    placeholderData: keepPreviousData,
  })
}
