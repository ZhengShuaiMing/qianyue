import './progress-bar.css'

/** 做完几项的进度条：条加右边「已完成 x/y」。label 是读屏用的名字 */
export function ProgressBar({ done, total, label = '完成进度' }: { done: number; total: number; label?: string }) {
  return (
    <div className="progress">
      <span role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}>
        <i style={{ width: `${total ? (done / total) * 100 : 0}%` }} />
      </span>
      <em>已完成 {done}/{total}</em>
    </div>
  )
}
