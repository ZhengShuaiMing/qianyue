import type { CSSProperties } from 'react'
import type { Job, JobCategory, JobPrep, JobStage } from '../../api/jobs'
import { sourceOf } from '../../lib/sources'
import { verdictTint } from '../../lib/verdict'

/** 应聘进展 */
export const STAGES: Record<JobStage, string> = {
  not_applied: '未投递',
  applied: '已投递',
  assessment: '已笔试',
  interviewed: '已面试',
  offer: '已获 Offer',
  rejected: '未通过',
  withdrawn: '已放弃',
}

/** 改成这个阶段时，填的那个日期叫什么 */
export const STAGE_DATES: Record<JobStage, string> = {
  not_applied: '日期',
  applied: '投递日期',
  assessment: '笔试日期',
  interviewed: '面试日期',
  offer: 'Offer 日期',
  rejected: '结果日期',
  withdrawn: '放弃日期',
}

/** 处理进度 */
export const PREPS: Record<JobPrep, string> = {
  incomplete: 'JD 不完整',
  unevaluated: '待评估',
  evaluated: '已评估',
}

export const CATEGORIES: { key: JobCategory | 'all'; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'not_started', label: '未开始' },
  { key: 'in_progress', label: '进行中' },
  { key: 'closed', label: '已结束' },
]

/** 岗位详情里七个平级的功能页 */
export const FEATURES = [
  { key: 'info', label: '岗位信息' },
  { key: 'evaluation', label: '评估' },
  { key: 'tailoring', label: '定制简历' },
  { key: 'research', label: '公司研究' },
  { key: 'communication', label: '沟通建议' },
  { key: 'interview_prep', label: '面试准备' },
  { key: 'mock', label: '模拟面试' },
] as const
export type Feature = (typeof FEATURES)[number]['key']



export const tintOf = (job: Job) => verdictTint(job.evaluation?.verdict)

/** 卡片上的一个词：投了以后看应聘进展；还没投时看处理进度（评估过的分数已经显示在旁边） */
export const cardLabel = (job: Job) =>
  job.stage !== 'not_applied' || job.prep === 'evaluated' ? STAGES[job.stage] : PREPS[job.prep]

/** 列表上的名字：公司名，没填就用职位名 */
export const jobName = (job: Job) => job.company || job.title || '未命名岗位'


/** 首字方块的颜色：按名字取一个稳定的色相，代替岗位没有的图片 */
export function tileStyle(job: Job): CSSProperties {
  let h = 7
  for (const ch of jobName(job)) h = (h * 131 + ch.codePointAt(0)!) >>> 0
  return { '--h': Math.round(((h * 0.618033988749895) % 1) * 360), '--dot': sourceOf(job.source).color } as CSSProperties
}
