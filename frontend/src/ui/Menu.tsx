import { useEffect, useId, useRef, type ReactNode } from 'react'
import { IconMore } from './icons'
import './glass.css'
import './icon-button.css'
import './menu.css'

export type MenuItem = { label: string; icon?: ReactNode; onSelect: () => void; danger?: boolean }

/** 「更多」菜单：圆形按钮，点开在它下方右对齐弹出一列操作。
    用浏览器的 popover：点别处或按 Esc 自动关；页面滚动时也关掉，免得和按钮错位 */
export function Menu({ label, items }: { label: string; items: MenuItem[] }) {
  const id = useId()
  const button = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = menu.current
    if (!el) return
    const place = (event: Event) => {
      if ((event as ToggleEvent).newState !== 'open' || !button.current) return
      const r = button.current.getBoundingClientRect()
      el.style.top = `${r.bottom + 8}px`
      el.style.right = `${window.innerWidth - r.right}px`
    }
    const close = () => el.hidePopover()
    el.addEventListener('beforetoggle', place)
    window.addEventListener('scroll', close, { passive: true })
    window.addEventListener('resize', close)
    return () => {
      el.removeEventListener('beforetoggle', place)
      window.removeEventListener('scroll', close)
      window.removeEventListener('resize', close)
    }
  }, [])

  return (
    <>
      <button ref={button} type="button" className="icon-btn glass" popoverTarget={id} aria-label={label} title={label}>
        <IconMore />
      </button>
      <div ref={menu} id={id} popover="auto" className="menu float" role="menu">
        {items.map((item) => (
          <button
            key={item.label}
            type="button"
            role="menuitem"
            data-danger={item.danger}
            onClick={() => { menu.current?.hidePopover(); item.onSelect() }}
          >
            {item.icon}{item.label}
          </button>
        ))}
      </div>
    </>
  )
}
