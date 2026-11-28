import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './client'

/** 字段来自 backend/app/api/search.py */
export type SearchType = 'material' | 'job' | 'communication' | 'artifact' | 'chat'
export type SearchHit = {
  type: SearchType
  /** 材料种类、成果种类，或沟通里谁说的；岗位是 jd */
  kind: string
  /** 出自哪里，例如「岗位评估 · 星河智能 — Agent 工程师」 */
  title: string
  heading: string
  text: string
  date: string
  /** 前端打开它的地址 */
  link: string
  job_id: number | null
  /** text 里和查询字面相同的位置 [开始, 结束) */
  highlights: [number, number][]
  /** 标题或正文里有和查询字面相同的词；false 表示只按意思找到 */
  literal: boolean
}

/** 全局检索：材料、岗位、沟通记录、成果一起找。q 为空时不请求 */
export function useSearch(q: string, type: SearchType | null) {
  return useQuery({
    queryKey: ['search', q, type],
    queryFn: () => {
      const params = new URLSearchParams({ q, limit: '20' })
      if (type) params.set('type', type)
      return api<SearchHit[]>(`/search?${params}`)
    },
    enabled: q.trim() !== '',
    staleTime: 30_000,
  })
}

/** 一条检索历史：同一个查询和类别只留一条 */
export type SearchRecord = { id: number; query: string; type: SearchType | null; hits: number; created_at: string }

export function useSearchHistory() {
  return useQuery({ queryKey: ['search-history'], queryFn: () => api<SearchRecord[]>('/search/history') })
}

/** 删一条（传编号）或清空全部（传 null） */
export function useDeleteSearch() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: number | null) => api<void>(id === null ? '/search/history' : `/search/history/${id}`, { method: 'DELETE' }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['search-history'] }),
  })
}
