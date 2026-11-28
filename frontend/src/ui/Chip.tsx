import type { CSSProperties, ReactNode } from 'react'
import './glass.css'
import './chip.css'

/** 小标签。dot 给一个颜色时前面带一个小圆点（招聘平台用它的品牌色）；tinted 用当前岗位的结论色 */
export function Chip({ children, dot, tinted }: { children: ReactNode; dot?: string; tinted?: boolean }) {
  return (
    <span className="chip glass" data-tinted={tinted} style={dot ? ({ '--dot': dot } as CSSProperties) : undefined}>
      {dot && <i />}{children}
    </span>
  )
}
