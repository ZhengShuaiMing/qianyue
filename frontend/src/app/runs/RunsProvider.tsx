import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { RUN_KINDS, useRuns } from '../../api/runs'
import { SNAPSHOT } from '../../demo/mode'
import { useToast } from '../../ui/toast'
import { RunsContext, type JobProgress, type RunsLive, type ToolUse } from './runs-context'

/** 盯着所有正在进行的运行：每个运行开一条进度流（SSE），记下每个岗位走到哪一步；
    某个岗位有了结论就刷新岗位列表，整个运行结束时弹一句轻提示。

    每个运行只开一次流：后端的流每次连接都从第一条重放，重连会让工具调用、采集进度重复一遍，
    已经结束的运行重连还会再弹一次完成提示。所以新出现的运行才开，结束的记下来不再开 */
export function RunsProvider({ children }: { children: ReactNode }) {
  const runs = useRuns()
  const items = useMemo(() => runs.data?.items ?? [], [runs.data])
  const running = items.filter((r) => r.status === 'running')
  const runningKey = running.map((r) => r.id).join(',')
  const [progress, setProgress] = useState<Record<string, JobProgress>>({})
  const [lines, setLines] = useState<Record<string, string[]>>({})
  const client = useQueryClient()
  const toast = useToast()
  const sources = useRef(new Map<string, EventSource>())
  const finished = useRef(new Set<string>())

  useEffect(() => {
    for (const run of running) {
      // 只读快照里没有后端可连
      if (SNAPSHOT || sources.current.has(run.id) || finished.current.has(run.id)) continue
      const source = new EventSource(`/api/runs/${run.id}/events`)
      sources.current.set(run.id, source)
      const update = (jobId: number, change: (p: JobProgress) => JobProgress) => setProgress((all) => {
        const key = `${run.id}:${jobId}`
        const before = all[key] ?? { runId: run.id, kind: run.kind, seen: [], labels: [], label: '', tools: [] }
        return { ...all, [key]: change(before) }
      })
      source.addEventListener('node', (e) => {
        const { job_id, node, label } = JSON.parse((e as MessageEvent).data) as { job_id: number; node: string; label: string }
        update(job_id, (p) => p.seen.includes(node) ? { ...p, label } : { ...p, seen: [...p.seen, node], labels: [...p.labels, label], label })
      })
      source.addEventListener('progress', (e) => {
        const { progress: line } = JSON.parse((e as MessageEvent).data) as { progress: string }
        setLines((all) => ({ ...all, [run.id]: [...(all[run.id] ?? []), line].slice(-200) }))
      })
      source.addEventListener('tool', (e) => {
        const { job_id, tool, label, detail } = JSON.parse((e as MessageEvent).data) as { job_id: number } & ToolUse
        update(job_id, (p) => ({ ...p, tools: [...p.tools, { tool, label, detail }] }))
      })
      source.addEventListener('job', () => {
        void client.invalidateQueries({ queryKey: ['jobs'] })
        void client.invalidateQueries({ queryKey: ['artifacts', 'list'] })
      })
      source.addEventListener('done', (e) => {
        source.close()
        sources.current.delete(run.id)
        finished.current.add(run.id)
        const { status, error } = JSON.parse((e as MessageEvent).data) as { status: string; error: string | null }
        const name = RUN_KINDS[run.kind] + (run.job_ids.length > 1 ? `（${run.job_ids.length} 个岗位）` : '')
        if (status === 'succeeded') toast(`${name}完成`)
        else if (status === 'failed') toast(`${name}没有完成：${error ?? '原因不明'}`, 'error')
        else if (status === 'cancelled') toast(`${name}已取消`)
        setProgress((all) => Object.fromEntries(Object.entries(all).filter(([, p]) => p.runId !== run.id)))
        setLines((all) => Object.fromEntries(Object.entries(all).filter(([id]) => id !== run.id)))
        void client.invalidateQueries({ queryKey: ['runs'] })
        void client.invalidateQueries({ queryKey: ['jobs'] })
        void client.invalidateQueries({ queryKey: ['artifacts', 'list'] })
      })
    }
    // 只在「正在进行的运行」这一组变了时看看有没有新的要连
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runningKey])
  // 离开页面时关掉还开着的流
  useEffect(() => {
    const open = sources.current
    return () => {
      open.forEach((source) => source.close())
      open.clear()
    }
  }, [])

  const value = useMemo<RunsLive>(() => {
    const ofJob = (jobId: number, kind: string) => items.filter((r) => r.kind === kind && r.job_ids.includes(jobId))
    return {
      runs: items,
      activeCount: items.filter((r) => r.status === 'running' || r.status === 'waiting_user').length,
      activeRun: (jobId, kind) => ofJob(jobId, kind).find((r) => r.status === 'running' || r.status === 'waiting_user'),
      latestRun: (jobId, kind) => ofJob(jobId, kind)[0],
      progress: (jobId, runId) => progress[`${runId}:${jobId}`],
      lines: (runId) => lines[runId] ?? [],
    }
  }, [items, progress, lines])

  return <RunsContext.Provider value={value}>{children}</RunsContext.Provider>
}
