import type { ReactNode } from 'react'
import './group.css'

/** 分组：像设置页一样，组名在外面，右边可以放一个操作；内容在一个圆角盒子里，行之间一条细线 */
export function Group({ id, label, hint, action, children, plain }: {
  /** 页面里可以用 #id 直接跳到这一组 */
  id?: string
  label: string
  hint?: ReactNode
  action?: ReactNode
  children: ReactNode
  /** 内容不放进盒子，直接排在组名下面 */
  plain?: boolean
}) {
  return (
    <section className="group" id={id}>
      <div className="group-head">
        <h3>{label}{hint && (typeof hint === 'string' ? <small className="clip" title={hint}>{hint}</small> : <small>{hint}</small>)}</h3>
        {action}
      </div>
      {plain ? children : <div className="box">{children}</div>}
    </section>
  )
}
