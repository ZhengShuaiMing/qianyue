import type { ComponentProps, ReactNode } from 'react'
import { NavLink } from 'react-router'
import './glass.css'
import './icon-button.css'

type Common = { label: string; children: ReactNode; /** sm：放在内容里的小按钮 */ size?: 'sm' }

/** 圆形玻璃按钮，只放一个图标；label 同时用作读屏文字和鼠标悬停提示 */
export function IconButton({ label, children, size, ...rest }: Common & Omit<ComponentProps<'button'>, 'children'>) {
  return (
    <button type="button" className="icon-btn glass" data-size={size} aria-label={label} title={label} {...rest}>{children}</button>
  )
}

/** 圆形按钮，点了下载一个文件 */
export function IconDownload({ label, children, size, href }: Common & { href: string }) {
  return <a className="icon-btn glass" data-size={size} href={href} download aria-label={label} title={label}>{children}</a>
}

/** 同样的圆形按钮，点了去另一个页面；在那个页面时保持按下的样子 */
export function IconLink({ label, children, to }: Common & { to: string }) {
  return (
    <NavLink className="icon-btn glass" to={to} aria-label={label} title={label}>{children}</NavLink>
  )
}
