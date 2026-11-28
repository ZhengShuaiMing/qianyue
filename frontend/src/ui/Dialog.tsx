import { useEffect, useId, useRef, type ReactNode } from 'react'
import './glass.css'
import './dialog.css'

/** 弹窗：用浏览器自带的 <dialog>，按 Esc 关闭、焦点留在弹窗里。
    标题在上，内容在中间（太长时只有这里滚动），操作按钮在底部右侧 */
export function Dialog({ open, onClose, onSubmit, title, children, actions, size, focusSelf }: {
  open: boolean
  onClose: () => void
  /** 给了就把内容和按钮包进一个表单：按回车或点 type="submit" 的按钮时调用 */
  onSubmit?: () => void
  title: string
  children: ReactNode
  actions: ReactNode
  /** wide：要填一段长文本的表单 */
  size?: 'wide'
  /** 打开时焦点放在弹窗本身，不自动落到第一个选项上（选项会因为有焦点而显示描边，像是被选中了） */
  focusSelf?: boolean
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) {
      dialog.showModal()
      if (focusSelf) dialog.focus()
    }
    if (!open && dialog.open) dialog.close()
  }, [open, focusSelf])

  return (
    <dialog ref={ref} className="dialog float" data-size={size} onClose={onClose} aria-labelledby={titleId} tabIndex={focusSelf ? -1 : undefined}>
      {open && (
        <>
          <h2 className="dialog-title" id={titleId}>{title}</h2>
          {onSubmit ? (
            <form className="dialog-form" onSubmit={(e) => { e.preventDefault(); onSubmit() }}>
              <div className="dialog-body">{children}</div>
              <div className="dialog-actions">{actions}</div>
            </form>
          ) : (
            <>
              <div className="dialog-body">{children}</div>
              <div className="dialog-actions">{actions}</div>
            </>
          )}
        </>
      )}
    </dialog>
  )
}
