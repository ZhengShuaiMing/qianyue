import { createContext, useContext } from 'react'

export type ToastTone = 'info' | 'error'
export type ShowToast = (text: string, tone?: ToastTone) => void

export const ToastContext = createContext<ShowToast | null>(null)

/** 轻提示：屏幕下方浮出一句话，几秒后自己消失。用于操作结果和连接状态这类不需要用户处理的消息 */
export function useToast(): ShowToast {
  const show = useContext(ToastContext)
  if (!show) throw new Error('useToast 要在 ToastProvider 里使用')
  return show
}
