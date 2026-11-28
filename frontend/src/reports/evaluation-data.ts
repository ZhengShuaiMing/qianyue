import type { Verdict } from '../api/jobs'

// 评估报告的结构化结果（artifact.data），字段来自 backend/app/llm/evaluation.py
export type DimensionScore = { score: number | null; evidence: string }
export type Blocker = { issue: string; status: 'confirmed' | 'pending'; evidence: string }
export type EvaluationData = {
  output: {
    company: string
    title: string
    direction: string
    summary: string
    keywords: string[]
    a_overview: string
    b_resume_match: string
    c_level: string
    d_compensation: string
    e_plan: string
    dimensions: Record<string, DimensionScore>
    blockers: Blocker[]
    key_unknowns: string[]
  }
  scorecard: {
    total: number
    coverage: number
    confidence: 'high' | 'medium' | 'low'
    verdict: Verdict
    confirmed_blockers: number
    pending_blockers: number
  }
  recheck_totals: number[]
  inputs: { conditions: string }
}

export const CONFIDENCE = { high: '高', medium: '中', low: '低' } as const
