import { useQuery } from '@tanstack/react-query'
import { api } from './client'

export type FunnelStep = 'total' | 'has_jd' | 'evaluated' | 'applied' | 'interviewed' | 'offer'
export type Period = { start: string; added: number; applied: number; interviewed: number }
export type TrendUnit = 'day' | 'week' | 'month'

/** 总览页的统计，字段来自 backend/app/api/overview.py */
export type Overview = {
  total: number
  /** 最近一次评估 4.0 分及以上 */
  worth: number
  /** 现在的阶段是「未通过」「放弃」的岗位 */
  rejected: number
  withdrawn: number
  evaluated: number
  average_score: number | null
  /** 后三步按「走到过」算：结束了的岗位看改阶段的记录，还在流程里的以现在的阶段为准 */
  funnel: { step: FunnelStep; count: number }[]
  /** 投递率占全部岗位；面试率、Offer 率占投递过的 */
  rates: { applied: number | null; interviewed: number | null; offer: number | null }
  platforms: { source: string; name: string; jobs: number; evaluated: number; average_score: number | null; applied: number; interviewed: number; offer: number; applied_rate: number | null }[]
  /** 从早到晚，最后一段包含今天：近 14 天、近 8 周、近 6 个月 */
  trend: Record<TrendUnit, Period[]>
  /** 评分分布，从高到低 */
  scores: { label: string; count: number }[]
  /** 评估过的每个岗位：最近一次评估的总分和添加日期，按添加先后 */
  points: { score: number; added: string }[]
}

// 放在 jobs 下面：岗位、阶段、评估有变化时跟着刷新
export function useOverview() {
  return useQuery({ queryKey: ['jobs', 'stats'], queryFn: () => api<Overview>('/overview') })
}
