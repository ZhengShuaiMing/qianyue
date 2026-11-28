import './brand-mark.css'

// 迁跃的品牌图：两段上升的斜笔画。
const LOWER = 'M10 52H18.5L30.870968 34.870968L30 34H21.5L9.129032 51.129032Z'
const UPPER = 'M34 30H42.5L54.870968 12.870968L54 12H45.5L33.129032 29.129032Z'

type Props = {
  className?: string
  /** 深色底版本（同 favicon）：墨黑圆角底，暖白笔画。浅色主题用它 */
  tile?: boolean
  /** 先描边、再填充的品牌动画 */
  animated?: boolean
}

export function BrandMark({ className = '', tile = false, animated = false }: Props) {
  return (
    <svg
      className={`brand-mark ${tile ? 'brand-mark--tile' : ''} ${animated ? 'brand-mark--animated' : ''} ${className}`}
      viewBox="0 0 64 64"
      role="img"
      aria-label="迁跃 Qianyue"
    >
      {tile && <rect className="brand-mark__tile" width="64" height="64" rx="14" />}
      <path className="brand-mark__stroke brand-mark__stroke--lower" d={LOWER} pathLength={1} />
      <path className="brand-mark__stroke brand-mark__stroke--upper" d={UPPER} pathLength={1} />
      <path className="brand-mark__fill" d={LOWER} />
      <path className="brand-mark__fill" d={UPPER} />
    </svg>
  )
}
