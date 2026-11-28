import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './client'

export type ArtifactKind =
  | 'evaluation' | 'tailored_resume' | 'company_research' | 'communication_advice' | 'interview_prep' | 'mock_interview' | 'profile'

export type ArtifactSummary = {
  id: number
  kind: ArtifactKind
  job_id: number | null
  run_id: string | null
  title: string
  score: number | null
  created_at: string
  updated_at: string
}
/** 评估报告的八个维度：名称、权重；不可评估时补什么信息能计分（只有部分维度有） */
export type DimensionInfo = { key: string; name: string; weight: number; how_to_score: string | null }
export type Artifact = ArtifactSummary & {
  content_md: string
  data: Record<string, unknown> | null
  /** 只有评估报告有 */
  dimensions: DimensionInfo[] | null
}

export const ARTIFACT_KINDS: Record<ArtifactKind, string> = {
  evaluation: '评估报告',
  tailored_resume: '定制简历',
  company_research: '公司研究',
  communication_advice: '沟通建议',
  interview_prep: '面试准备',
  mock_interview: '模拟面试复盘',
  profile: '能力画像',
}

const PAGE = 50

/** 全部成果，最近更新的在前，可以按类型筛 */
export function useArtifacts(kind: ArtifactKind | null) {
  return useInfiniteQuery({
    queryKey: ['artifacts', 'list', kind],
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams({ limit: String(PAGE), offset: String(pageParam) })
      if (kind) params.set('kind', kind)
      return api<{ items: ArtifactSummary[]; total: number }>(`/artifacts?${params}`)
    },
    initialPageParam: 0,
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((n, p) => n + p.items.length, 0)
      return loaded < last.total ? loaded : undefined
    },
    placeholderData: keepPreviousData,
  })
}

export function useArtifact(id: number | null) {
  return useQuery({ queryKey: ['artifacts', 'detail', id], queryFn: () => api<Artifact>(`/artifacts/${id}`), enabled: id !== null })
}

/** 这个岗位最近一份某类成果，没有是 null */
export function useLatestArtifact(jobId: number, kind: ArtifactKind) {
  return useQuery({
    queryKey: ['artifacts', 'list', 'latest', jobId, kind],
    queryFn: async () => {
      const list = await api<{ items: ArtifactSummary[] }>(`/artifacts?job_id=${jobId}&kind=${kind}&limit=1`)
      return list.items[0] ?? null
    },
  })
}

/** 删除成果。删掉的评估报告不再复用：再评估这个岗位会重新评一次 */
export function useDeleteArtifact() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api<void>(`/artifacts/${id}`, { method: 'DELETE' }),
    // 不等刷新完：列表一刷新，删掉的那份就从页面上消失，发起删除的组件会被卸载，
    // 调用方在 mutate 里写的「删完提示、跳走」就来不及执行了。
    // 只刷新列表和岗位，不动这份的详情：页面上它还显示着，重新取只会得到 404
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['artifacts', 'list'] })
      void client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

/** 导出 Markdown 的下载地址。后端给的文件名就是成果标题 */
export const exportUrl = (id: number) => `/api/artifacts/${id}/export`
/** 一次导出全部成果（或某一类）：一个 zip，每份成果一个 Markdown 文件 */
export const exportAllUrl = (kind: ArtifactKind | null) => `/api/artifacts/export${kind ? `?kind=${kind}` : ''}`
