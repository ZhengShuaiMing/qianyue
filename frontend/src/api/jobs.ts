import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { openAgentHandoff, screenshotHandoff } from './agent-handoff'
import { api, apiWithStatus, sendJson } from './client'
import type { Run } from './runs'

/** 应聘进展：只由你改 */
export type JobStage =
  | 'not_applied' | 'applied' | 'assessment' | 'interviewed' | 'offer' | 'rejected' | 'withdrawn'
export type JobCategory = 'not_started' | 'in_progress' | 'closed'
/** 处理进度：按有没有 JD、有没有评估报告算出来的，不能改 */
export type JobPrep = 'incomplete' | 'unevaluated' | 'evaluated'
export type JobSort = 'recent' | 'score'
export type Verdict = 'priority' | 'worth' | 'conditional' | 'not_recommended'

export type Job = {
  id: number
  source: string
  url: string | null
  company: string
  title: string
  city: string | null
  salary: string | null
  stage: JobStage
  prep: JobPrep
  has_jd: boolean
  note: string
  created_at: string
  updated_at: string
  /** 看起来和这个更早的岗位是同一个（换了名字或在别的平台又发了一次），只提示 */
  similar_to: number | null
  /** 最近一次评估，没评估过是 null */
  evaluation: { artifact_id: number; score: number | null; verdict: Verdict | null } | null
  /** 走到现在这个阶段是哪天（YYYY-MM-DD），没改过阶段是 null */
  stage_on: string | null
}

/** happened_on：这一步是哪天发生的（改阶段时填的，YYYY-MM-DD）；created_at：记下来的时间 */
export type JobEvent = { from_stage: JobStage | null; to_stage: JobStage; happened_on: string; note: string; created_at: string }
export type JobDetail = Job & {
  jd_text: string
  events: JobEvent[]
  /** 采集时从平台列表读到的；手动添加的一般没有 */
  company_size: string | null
  industry: string | null
  /** 平台显示的发布日期（猎聘是刷新日期） */
  posted_at: string | null
}
export type JobChanges = Partial<Pick<JobDetail, 'company' | 'title' | 'city' | 'salary' | 'jd_text' | 'note' | 'url' | 'source'>>
export type StageChange = { stage: JobStage; note: string; happened_on: string }

export type JobOverview = { total: number; categories: Record<JobCategory, number>; stages: Record<JobStage, number> }

export type NewJob = { jd_text: string; url: string | null; company: string; title: string; city: string | null; salary: string | null }

const PAGE = 50

/** 岗位列表，按 sort 排序（服务端排，翻页也对）。一次取 50 个，往下再取 */
export function useJobs(filter: { category: JobCategory | null; q: string; sort: JobSort }) {
  return useInfiniteQuery({
    queryKey: ['jobs', 'list', filter],
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams({ limit: String(PAGE), offset: String(pageParam) })
      if (filter.category) params.set('category', filter.category)
      if (filter.q) params.set('q', filter.q)
      if (filter.sort !== 'recent') params.set('sort', filter.sort)
      return api<{ items: Job[]; total: number }>(`/jobs?${params}`)
    },
    initialPageParam: 0,
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((n, p) => n + p.items.length, 0)
      return loaded < last.total ? loaded : undefined
    },
    // 换筛选条件时先留着上一份列表，新结果到了再换，不闪空
    placeholderData: keepPreviousData,
  })
}

/** 看板要一次看到全部岗位：按每页 200 个连着取完。搜索词和排序和列表一样 */
export function useAllJobs(filter: { q: string; sort: JobSort }) {
  return useQuery({
    queryKey: ['jobs', 'all', filter],
    queryFn: async () => {
      const items: Job[] = []
      for (;;) {
        const params = new URLSearchParams({ limit: '200', offset: String(items.length) })
        if (filter.q) params.set('q', filter.q)
        if (filter.sort !== 'recent') params.set('sort', filter.sort)
        const page = await api<{ items: Job[]; total: number }>(`/jobs?${params}`)
        items.push(...page.items)
        if (!page.items.length || items.length >= page.total) return items
      }
    },
    placeholderData: keepPreviousData,
  })
}

/** 单个岗位的详情：JD 全文和阶段变更记录 */
export function useJob(id: number | null) {
  return useQuery({
    queryKey: ['jobs', 'detail', id],
    queryFn: () => api<JobDetail>(`/jobs/${id}`),
    enabled: id !== null,
  })
}

/** 改岗位信息或 JD */
export function useUpdateJob(id: number) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (changes: JobChanges) => api<JobDetail>(`/jobs/${id}`, sendJson('PATCH', changes)),
    onSuccess: (job) => {
      client.setQueryData(['jobs', 'detail', id], job)
      return client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

/** 改阶段。设成当前阶段不会多一条记录 */
export function useChangeStage(id: number) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (change: StageChange) => api<JobDetail>(`/jobs/${id}/stage`, sendJson('POST', change)),
    onSuccess: (job) => {
      client.setQueryData(['jobs', 'detail', id], job)
      return client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

/** 每个类别有多少岗位 */
export function useJobOverview() {
  return useQuery({ queryKey: ['jobs', 'overview'], queryFn: () => api<JobOverview>('/jobs/overview') })
}

/** 添加岗位。同一链接或同一份 JD 已经有了时，后端返回 200 和已有的那个；带截图时由后端交给模型读出岗位内容，和填的内容一起保存。
    没有模型 Key 时带截图的返回 202 和一次识别截图运行：截图已存下，弹出「交给你的 Agent」 */
export function useAddJob() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async ({ images = [], ...job }: NewJob & { images?: string[] }): Promise<{ job: Job; created: boolean } | { handedOff: Run }> => {
      const path = images.length ? '/jobs/from-screenshots' : '/jobs'
      const { status, data } = await apiWithStatus<Job | Run>(path, sendJson('POST', images.length ? { ...job, images } : job))
      if (status === 202) return { handedOff: data as Run }
      return { job: data as Job, created: status === 201 }
    },
    onSuccess: (result) => {
      if ('handedOff' in result) openAgentHandoff(screenshotHandoff(result.handedOff, 'job', client))
      return client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

/** 删除岗位：列表不再显示；以后再添加同一个链接或 JD 会恢复 */
export function useDeleteJob() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api<void>(`/jobs/${id}`, { method: 'DELETE' }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['jobs'] }),
  })
}
