import type { ReactNode } from 'react'
import { Button } from './Button'
import { Dialog } from './Dialog'

/** 确认一次不能轻易撤回的操作（删除）。失败时弹窗不关，把原因写在说明的位置 */
export function ConfirmDialog({ open, title, children, confirmLabel, busy, error, onConfirm, onClose }: {
  open: boolean
  title: string
  children: ReactNode
  confirmLabel: string
  busy: boolean
  error: string | null
  onConfirm: () => void
  onClose: () => void
}) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      actions={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button variant="danger" onClick={onConfirm} disabled={busy}>{busy ? '正在处理…' : confirmLabel}</Button>
        </>
      }
    >
      <p className="dialog-text" data-tone={error ? 'error' : undefined}>{error ? `没有成功：${error}` : children}</p>
    </Dialog>
  )
}
