import type { ComponentProps } from 'react'
import { Link } from 'react-router'
import './glass.css'
import './button.css'

type Look = {
  /** primary：这个区域里完成主要任务的那一个操作；danger：删除这类不能轻易撤回的操作 */
  variant?: 'glass' | 'primary' | 'danger'
  /** lg：页头工具条；sm：行内的小操作 */
  size?: 'sm' | 'lg'
}

const classOf = (variant: Look['variant']) => (variant === 'glass' || !variant ? 'btn glass' : 'btn')

/** 胶囊按钮。图标放在文字前面，作为 children 一起传进来 */
export function Button({ variant = 'glass', size, type = 'button', ...rest }: Look & ComponentProps<'button'>) {
  return <button type={type} className={classOf(variant)} data-variant={variant} data-size={size} {...rest} />
}

/** 长得像按钮、点了下载一个文件（后端接口给的附件） */
export function ButtonDownload({ variant = 'glass', size, ...rest }: Look & ComponentProps<'a'>) {
  return <a className={classOf(variant)} data-variant={variant} data-size={size} download {...rest} />
}

/** 长得像按钮、点了去另一个页面 */
export function ButtonLink({ variant = 'glass', size, ...rest }: Look & ComponentProps<typeof Link>) {
  return <Link className={classOf(variant)} data-variant={variant} data-size={size} {...rest} />
}
