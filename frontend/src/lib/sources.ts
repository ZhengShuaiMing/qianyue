/** 来源：四个平台用各自的品牌色，只出现在小圆点上；手动添加用中性色 */
export const SOURCES: { key: string; label: string; color: string }[] = [
  { key: 'manual', label: '手动添加', color: 'var(--ink-3)' },
  { key: 'boss', label: 'BOSS 直聘', color: '#03c0c1' },
  { key: 'zhaopin', label: '智联招聘', color: '#54bcfa' },
  { key: 'liepin', label: '猎聘', color: '#ff7956' },
  { key: '51job', label: '前程无忧', color: '#e9233e' },
]
export const sourceOf = (key: string) => SOURCES.find((s) => s.key === key) ?? { key, label: key, color: 'var(--ink-3)' }
