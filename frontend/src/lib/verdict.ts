import type { Verdict } from '../api/jobs'

export const VERDICTS: Record<Verdict, string> = {
  priority: '强匹配，建议优先投递',
  worth: '匹配良好，值得投递',
  conditional: '有条件匹配，确认关键风险后再投',
  not_recommended: '不建议投入主要精力',
}

/** 结论色：低饱和的莫兰迪色。给分数、结论标签，也以 18% 混进背景光 */
const TINT: Record<Verdict | 'none', string> = {
  priority: '#84b598',
  worth: '#84b598',
  conditional: '#d2a473',
  not_recommended: '#9d9bb4',
  none: '#86a8cf',
}
/** 正在评估时的颜色：灰青 */
export const RUNNING_TINT = '#7fb3b5'
export const verdictTint = (verdict: Verdict | null | undefined) => TINT[verdict ?? 'none']
export const fmtScore = (score: number | null | undefined) => (score == null ? '—' : score.toFixed(1))
