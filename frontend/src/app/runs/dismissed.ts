import { useSyncExternalStore } from 'react'
import type { Run } from '../../api/runs'

/** 关掉的「没有完成」提示，记在这个浏览器里。按运行和它最后一次变化的时间记：
    从断点继续后又没做完，会重新提示 */
const KEY = 'qianyue.dismissed-runs'
const LIMIT = 200
const listeners = new Set<() => void>()

function read(): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(KEY) ?? '[]')
    return Array.isArray(value) ? value : []
  } catch {
    return []
  }
}

let current = read()
const mark = (run: Run) => `${run.id}@${run.updated_at}`

export function dismissRun(run: Run) {
  current = [...current.filter((m) => m !== mark(run)), mark(run)].slice(-LIMIT)
  try { localStorage.setItem(KEY, JSON.stringify(current)) } catch { /* 存不了就只在这次打开时有效 */ }
  listeners.forEach((listener) => listener())
}

/** 返回一个判断函数：这次运行的提示是不是已经关掉了 */
export function useDismissed(): (run: Run) => boolean {
  const list = useSyncExternalStore(
    (listener) => { listeners.add(listener); return () => listeners.delete(listener) },
    () => current,
  )
  return (run) => list.includes(mark(run))
}
