import { useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode, type RefObject } from 'react'
import { NavLink, useLocation } from 'react-router'
import './glass.css'
import './segmented.css'

/** 胶囊分段控件：选中的底块会滑过去。用于页内切换 */
export function Segmented<K extends string>({ items, value, onChange, label, size, fill, trailing }: {
  items: { key: K; label: ReactNode }[]
  value: K
  onChange: (key: K) => void
  label: string
  size?: 'lg'
  /** 占满一行、各项平分宽度：一组平级的功能页 */
  fill?: boolean
  /** 放在胶囊里最右边、和分段隔一条竖线的一个小控件（例如排序） */
  trailing?: ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)
  const pill = useSlidingPill(ref, value, items.length)
  return (
    <div className="seg glass" data-size={size} data-fill={fill} role="tablist" aria-label={label} ref={ref}>
      <span className="seg-pill" style={pill} />
      {items.map((it) => (
        <button key={it.key} type="button" role="tab" aria-selected={it.key === value} onClick={() => onChange(it.key)}>
          {it.label}
        </button>
      ))}
      {trailing && <span className="seg-trailing">{trailing}</span>}
    </div>
  )
}

/** 同样的胶囊，但每项是一个链接。用于在页面之间导航 */
export function SegmentedNav({ items, label, size }: {
  items: { to: string; label: ReactNode }[]
  label: string
  size?: 'lg'
}) {
  const ref = useRef<HTMLElement>(null)
  // 当前页由路由决定，用 pathname 触发重新测量
  const pill = useSlidingPill(ref, useLocation().pathname, items.length)
  return (
    <nav className="seg glass" data-size={size} aria-label={label} ref={ref}>
      <span className="seg-pill" style={pill} />
      {items.map((it) => <NavLink key={it.to} to={it.to}>{it.label}</NavLink>)}
    </nav>
  )
}

/** 量出选中项的位置和宽度。选中项变了、窗口宽度变了、字体加载完、标签文字变长变短，都要重新量 */
function useSlidingPill(ref: RefObject<HTMLElement | null>, value: string, count: number) {
  const [pill, setPill] = useState<CSSProperties>({ opacity: 0 })
  useLayoutEffect(() => {
    const root = ref.current
    if (!root) return
    const measure = () => {
      const el = root.querySelector<HTMLElement>('[aria-selected="true"], [aria-current="page"]')
      setPill(el ? { width: el.offsetWidth, transform: `translateX(${el.offsetLeft}px)`, opacity: 1 } : { opacity: 0 })
    }
    // 放不下要左右滑时（窗口变窄，不只是手机）：选中项滚到看得见的位置；哪边还有内容，哪边就渐隐
    const edges = () => {
      const more = root.scrollWidth - root.clientWidth > 1
      root.dataset.scroll = String(more)
      root.dataset.moreStart = String(more && root.scrollLeft > 1)
      root.dataset.moreEnd = String(more && root.scrollLeft < root.scrollWidth - root.clientWidth - 1)
    }
    const reveal = () => {
      const el = root.querySelector<HTMLElement>('[aria-selected="true"], [aria-current="page"]')
      if (!el) return
      const pad = 24
      if (el.offsetLeft - pad < root.scrollLeft) root.scrollLeft = el.offsetLeft - pad
      else if (el.offsetLeft + el.offsetWidth + pad > root.scrollLeft + root.clientWidth) {
        root.scrollLeft = el.offsetLeft + el.offsetWidth + pad - root.clientWidth
      }
    }
    measure()
    reveal()
    edges()
    const observer = new ResizeObserver(() => { measure(); edges() })
    root.querySelectorAll(':scope > button, :scope > a').forEach((el) => observer.observe(el))
    observer.observe(root)
    // 用普通鼠标的滚轮（只能上下滚）也能左右滑：放不下时，在胶囊上滚滚轮就横着滑
    const wheel = (e: WheelEvent) => {
      if (root.dataset.scroll !== 'true' || Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return
      const max = root.scrollWidth - root.clientWidth
      if ((e.deltaY < 0 && root.scrollLeft <= 0) || (e.deltaY > 0 && root.scrollLeft >= max)) return
      e.preventDefault()
      root.scrollBy({ left: e.deltaY, behavior: 'auto' })
    }
    root.addEventListener('scroll', edges, { passive: true })
    root.addEventListener('wheel', wheel, { passive: false })
    return () => { observer.disconnect(); root.removeEventListener('scroll', edges); root.removeEventListener('wheel', wheel) }
  }, [ref, value, count])
  return pill
}
