import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { ToastContext, type ToastTone } from './toast'
import './glass.css'
import './toast.css'

type Toast = { id: number; text: string; tone: ToastTone }

const SHOW_MS = 3200
const MAX = 3

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(1)
  const timers = useRef(new Map<number, number>())

  const dismiss = useCallback((id: number) => {
    setToasts((list) => list.filter((t) => t.id !== id))
    window.clearTimeout(timers.current.get(id))
    timers.current.delete(id)
  }, [])

  const show = useCallback((text: string, tone: ToastTone = 'info') => {
    const id = nextId.current++
    // 同一句话还在显示就不再叠一条
    setToasts((list) => (list.some((t) => t.text === text) ? list : [...list, { id, text, tone }].slice(-MAX)))
    timers.current.set(id, window.setTimeout(() => dismiss(id), SHOW_MS))
  }, [dismiss])

  useEffect(() => {
    const pending = timers.current
    return () => pending.forEach((timer) => window.clearTimeout(timer))
  }, [])

  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <button key={t.id} type="button" className="toast float" data-tone={t.tone} onClick={() => dismiss(t.id)} title="点击关闭">
            <i />{t.text}
          </button>
        ))}
      </div>
    </ToastContext.Provider>
  )
}
