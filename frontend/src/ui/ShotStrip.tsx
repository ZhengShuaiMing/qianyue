import type { Shot } from './use-screenshots'
import { IconClose } from './icons'
import './screenshots.css'

/** 截图的小预览：点开看原图，右上角去掉，最后一格再加一张 */
export function ShotStrip({ shots, onRemove, onMore, disabled }: { shots: Shot[]; onRemove: (id: string) => void; onMore: () => void; disabled?: boolean }) {
  return (
    <ul className="shot-list">
      {shots.map((s, i) => (
        <li key={s.id}>
          <a href={s.url} target="_blank" rel="noreferrer" title={`看第 ${i + 1} 张原图`}><img src={s.url} alt={`第 ${i + 1} 张截图`} /></a>
          <button type="button" className="shot-remove" aria-label={`去掉第 ${i + 1} 张`} title="去掉" onClick={() => onRemove(s.id)} disabled={disabled}><IconClose /></button>
        </li>
      ))}
      <li><button type="button" className="shot-more" onClick={onMore} aria-label="再加一张" title="再加一张" disabled={disabled}>+</button></li>
    </ul>
  )
}
