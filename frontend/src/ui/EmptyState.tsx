import type { ReactNode } from 'react'
import './empty-state.css'

/** 还没有内容时的说明框：虚线圆角盒子，一个标题、一句说明，可以带一个开始的操作 */
export function EmptyState({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="empty">
      <p className="empty-title">{title}</p>
      <p className="empty-text">{children}</p>
      {action && <div className="empty-action">{action}</div>}
    </section>
  )
}
