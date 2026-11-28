import type { JobProgress } from '../../app/runs/runs-context'

// 评估的节点，按先后。复评只在总分落在分数线附近时才有，插在校验和保存之间
export const STEPS = [
  { node: 'load_materials', label: '读取材料' },
  { node: 'precheck', label: '检查材料' },
  { node: 'call_model', label: '调用模型' },
  { node: 'validate', label: '校验输出' },
  { node: 'recheck', label: '复评', optional: true },
  { node: 'save', label: '保存报告' },
]

/** 走到第几步（0 起），给卡片底部的进度条用 */
export function stepRatio(progress: JobProgress | undefined): number {
  if (!progress) return 0
  const main = STEPS.filter((s) => !s.optional)
  const at = Math.max(...progress.seen.map((n) => main.findIndex((s) => s.node === n)), -1)
  return (at + 1) / (main.length + 1)
}

