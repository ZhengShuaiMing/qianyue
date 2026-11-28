import { useEffect, useRef } from 'react'
import { enterSample, exitSample, SNAPSHOT } from '../../demo/mode'
import { IconClose } from '../../ui/icons'
import type { GuideMode } from './guide-store'

/** 引导的入口：第一次打开迁跃时自动弹出（标题「第一次使用迁跃？先看教程」），之后点顶栏问号再打开（标题「使用引导」）。
    三个选择：查看示例数据（不走引导，只切到示例；已经在示例里时换成退出示例）、本页引导、全部引导 */
export function GuideIntro({ first, here, hasPage, onChoose }: {
  first: boolean
  here: string
  hasPage: boolean
  onChoose: (mode: GuideMode | null) => void
}) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = ref.current
    if (dialog && !dialog.open) dialog.showModal()
    // 打开时焦点放在「全部引导」上，按回车就开始
    dialog?.querySelector<HTMLElement>('[data-primary]')?.focus()
  }, [])
  return (
    <dialog ref={ref} className="guide-intro" aria-labelledby="guide-intro-title" onClose={() => onChoose(null)}>
      <button type="button" className="guide-intro-close" onClick={() => onChoose(null)} aria-label="关闭"><IconClose /></button>
      <h2 id="guide-intro-title">{first ? <>第一次使用迁跃？<br />先看教程</> : '使用引导'}</h2>
      <p>
        {__DEMO__
          ? '引导会带你逐个认识迁跃的每个功能，随时可以点右上角退出。'
          : '引导会切换到示例数据演示每个功能，随时可以点右上角退出，不会影响你自己的数据。'}
      </p>
      <div className="guide-intro-actions">
        {!SNAPSHOT && <button type="button" className="guide-intro-btn" onClick={() => { onChoose(null); enterSample(here) }}>查看示例数据</button>}
        {SNAPSHOT && !__DEMO__ && <button type="button" className="guide-intro-btn" onClick={() => { onChoose(null); exitSample(here) }}>退出示例数据</button>}
        <button type="button" className="guide-intro-btn" onClick={() => onChoose('page')} disabled={!hasPage}>本页引导</button>
        <button type="button" className="guide-intro-btn" data-primary onClick={() => onChoose('all')}>全部引导</button>
      </div>
    </dialog>
  )
}
