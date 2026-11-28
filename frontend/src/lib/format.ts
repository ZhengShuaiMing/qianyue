const pad = (n: number) => String(n).padStart(2, '0')

/** 后端的时间（带时区的 ISO 字符串）显示成「11-17 14:03」，不是今年的带上年份 */
export function fmtTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const day = `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  return d.getFullYear() === new Date().getFullYear() ? day : `${d.getFullYear()}-${day}`
}

/** 只有日期的「2026-11-17」显示成「11-17」，不是今年的带上年份 */
export function fmtDay(day: string): string {
  const [y, m, d] = day.split('-')
  return Number(y) === new Date().getFullYear() ? `${m}-${d}` : day
}

/** 对话里每条话的时间：今天的只写「14:03」，别的日子同 fmtTime */
export function fmtClock(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toDateString() === new Date().toDateString() ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : fmtTime(iso)
}

/** 今天的日期，YYYY-MM-DD，给日期输入框当默认值 */
export function today(): string {
  const d = new Date()
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}
