import { useEffect, useId, useRef, useState } from 'react'
import { IconCalendar, IconChevronLeft, IconChevronRight } from './icons'
import './date-picker.css'

const WEEK = ['一', '二', '三', '四', '五', '六', '日']
const pad = (n: number) => String(n).padStart(2, '0')
const iso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
const parse = (value: string) => { const [y, m, d] = value.split('-').map(Number); return new Date(y, m - 1, d) }
const shift = (d: Date, days: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + days)

/** 显示成「11月18日 周三 · 今天」 */
function spoken(value: string, today: string) {
  const d = parse(value)
  const yesterday = iso(shift(parse(today), -1))
  const near = value === today ? ' · 今天' : value === yesterday ? ' · 昨天' : ''
  const year = d.getFullYear() === parse(today).getFullYear() ? '' : `${d.getFullYear()}年`
  return `${year}${d.getMonth() + 1}月${d.getDate()}日 周${WEEK[(d.getDay() + 6) % 7]}${near}`
}

/** 选日期：点开是一个月历，min 到 max 以外的日子不能选；下面有「今天」「昨天」。
    用浏览器的 popover，在弹窗里也浮在最上层；点别处或按 Esc 关掉 */
export function DatePicker({ id, value, onChange, min, max }: { id?: string; value: string; onChange: (value: string) => void; min?: string; max: string }) {
  const panelId = useId()
  const trigger = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLDivElement>(null)
  const [month, setMonth] = useState(() => { const d = parse(value); return new Date(d.getFullYear(), d.getMonth(), 1) })

  useEffect(() => {
    const el = panel.current
    if (!el) return
    const place = (event: Event) => {
      if ((event as ToggleEvent).newState !== 'open' || !trigger.current) return
      const d = parse(value)
      setMonth(new Date(d.getFullYear(), d.getMonth(), 1))
      const r = trigger.current.getBoundingClientRect()
      const below = window.innerHeight - r.bottom > 380
      el.style.left = `${r.left}px`
      el.style.top = below ? `${r.bottom + 8}px` : ''
      el.style.bottom = below ? '' : `${window.innerHeight - r.top + 8}px`
    }
    el.addEventListener('beforetoggle', place)
    return () => el.removeEventListener('beforetoggle', place)
  }, [value])

  const pick = (day: string) => { onChange(day); panel.current?.hidePopover(); trigger.current?.focus() }
  const first = shift(month, -((month.getDay() + 6) % 7)) // 这个月第一天所在那周的周一
  const days = Array.from({ length: 42 }, (_, i) => shift(first, i))
  const lastMonthDay = new Date(month.getFullYear(), month.getMonth() + 1, 0)
  const rows = days.slice(0, Math.ceil((((month.getDay() + 6) % 7) + lastMonthDay.getDate()) / 7) * 7)
  const canNext = iso(new Date(month.getFullYear(), month.getMonth() + 1, 1)) <= max
  const canPrev = min === undefined || iso(new Date(month.getFullYear(), month.getMonth(), 0)) >= min
  const yesterday = iso(shift(parse(max), -1))

  return (
    <>
      <button ref={trigger} id={id} type="button" className="date-trigger" popoverTarget={panelId}>
        <span>{spoken(value, max)}</span>
        <IconCalendar />
      </button>
      <div ref={panel} id={panelId} popover="auto" className="date-panel float" role="dialog" aria-label="选日期">
        <div className="date-head">
          <button type="button" aria-label="上个月" disabled={!canPrev} onClick={() => setMonth((m) => new Date(m.getFullYear(), m.getMonth() - 1, 1))}><IconChevronLeft /></button>
          <strong>{month.getFullYear()}年{month.getMonth() + 1}月</strong>
          <button type="button" aria-label="下个月" disabled={!canNext} onClick={() => setMonth((m) => new Date(m.getFullYear(), m.getMonth() + 1, 1))}><IconChevronRight /></button>
        </div>
        <div className="date-grid">
          {WEEK.map((w) => <span key={w} className="date-week">{w}</span>)}
          {rows.map((d) => {
            const day = iso(d)
            return (
              <button
                key={day}
                type="button"
                className="date-day"
                data-outside={d.getMonth() !== month.getMonth()}
                data-today={day === max}
                aria-pressed={day === value}
                disabled={day > max || (min !== undefined && day < min)}
                onClick={() => pick(day)}
              >
                {d.getDate()}
              </button>
            )
          })}
        </div>
        <div className="date-quick">
          <button type="button" onClick={() => pick(max)} aria-pressed={value === max}>今天</button>
          {(min === undefined || yesterday >= min) && <button type="button" onClick={() => pick(yesterday)} aria-pressed={value === yesterday}>昨天</button>}
        </div>
      </div>
    </>
  )
}
