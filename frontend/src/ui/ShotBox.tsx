import type { ComponentProps } from 'react'
import { ShotStrip } from './ShotStrip'
import type { useScreenshots } from './use-screenshots'
import './screenshots.css'

type Shots = ReturnType<typeof useScreenshots>

/** 能放截图的输入框：截图的小图排在框里上面，下面照常打字。截图粘贴、拖进这个框就加上 */
export function ShotBox({ shots, size, disabled, ...props }: { shots: Shots; size?: 'short' } & ComponentProps<'textarea'>) {
  const has = shots.shots.length > 0
  return (
    <div className="shot-box" data-size={size} data-has-shots={has} data-disabled={disabled} {...shots.area}>
      {has && <ShotStrip shots={shots.shots} onRemove={shots.remove} onMore={shots.pick} disabled={disabled} />}
      <textarea className="shot-box-text" disabled={disabled} {...props} />
      {shots.input}
    </div>
  )
}

/** 字段说明里的那句：把什么粘贴、拖进来，或者选择图片 */
export function ShotHint({ what, onPick }: { what: string; onPick: () => void }) {
  return <>把{what}粘贴、拖进来，或者<button type="button" className="shot-pick" onClick={onPick}>选择图片</button></>
}
