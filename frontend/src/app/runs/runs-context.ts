import { createContext, useContext } from 'react'
import type { Run, RunKind } from '../../api/runs'

/** Agent 用了一次工具：搜了什么、读了哪个网页 */
export type ToolUse = { tool: string; label: string; detail: string }
/** 一个岗位在一次运行里走到哪一步。seen 是按先后经过的节点，labels 是它们的名字，tools 是用过的工具 */
export type JobProgress = { runId: string; kind: RunKind; seen: string[]; labels: string[]; label: string; tools: ToolUse[] }

export type RunsLive = {
  /** 最近的运行，最新的在前 */
  runs: Run[]
  /** 正在进行或等你处理的运行数量，顶栏任务按钮上的数字 */
  activeCount: number
  /** 这个岗位正在进行的某类运行 */
  activeRun: (jobId: number, kind: RunKind) => Run | undefined
  /** 这个岗位最近一次某类运行（不管状态） */
  latestRun: (jobId: number, kind: RunKind) => Run | undefined
  progress: (jobId: number, runId: string) => JobProgress | undefined
  /** 不针对岗位的运行（采集）一句一句的进度，最新的在最后 */
  lines: (runId: string) => string[]
}

export const RunsContext = createContext<RunsLive | null>(null)

export function useRunsLive(): RunsLive {
  const value = useContext(RunsContext)
  if (!value) throw new Error('useRunsLive 要在 RunsProvider 里使用')
  return value
}
