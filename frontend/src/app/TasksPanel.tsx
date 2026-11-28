import { useQueries } from '@tanstack/react-query'
import { useEffect, useId, useRef } from 'react'
import { useNavigate } from 'react-router'
import { api } from '../api/client'
import type { Job } from '../api/jobs'
import { RUN_KINDS, useRunAction, type Run } from '../api/runs'
import { fmtTime } from '../lib/format'
import { Button } from '../ui/Button'
import { IconTasks } from '../ui/icons'
import { ProgressBar } from '../ui/ProgressBar'
import { useToast } from '../ui/toast'
import { CloseNotice } from '../pages/jobs/RunViews'
import { useDismissed } from './runs/dismissed'
import { runLink } from './runs/run-link'
import { useRunsLive } from './runs/runs-context'
import '../ui/glass.css'
import '../ui/icon-button.css'
import './tasks-panel.css'

const GROUPS: { label: string; match: (r: Run) => boolean }[] = [
  { label: '进行中', match: (r) => r.status === 'running' },
  { label: '等你处理', match: (r) => r.status === 'waiting_user' },
  { label: '没有完成', match: (r) => r.status === 'failed' || r.status === 'cancelled' },
  { label: '已完成', match: (r) => r.status === 'succeeded' },
]

/** 顶栏的「任务」：所有运行按进行中、等你处理、没有完成、已完成分组；可以取消、继续、打开结果 */
export function TasksButton() {
  const { runs: all, activeCount } = useRunsLive()
  const dismissed = useDismissed()
  // 关掉的「没有完成」不再列出
  const runs = all.filter((r) => !dismissed(r))
  const id = useId()
  const button = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = panel.current
    if (!el) return
    const place = (event: Event) => {
      if ((event as ToggleEvent).newState !== 'open' || !button.current) return
      const r = button.current.getBoundingClientRect()
      el.style.top = `${r.bottom + 10}px`
      el.style.right = `${Math.max(16, window.innerWidth - r.right)}px`
    }
    el.addEventListener('beforetoggle', place)
    return () => el.removeEventListener('beforetoggle', place)
  }, [])

  const label = activeCount ? `任务，${activeCount} 个在进行或等你处理` : '任务'
  return (
    <>
      <button ref={button} type="button" className="icon-btn glass" popoverTarget={id} aria-label={label} title={label}>
        <IconTasks />
        {activeCount > 0 && <span className="tasks-badge">{activeCount}</span>}
      </button>
      <div ref={panel} id={id} popover="auto" className="tasks float" aria-label="任务">
        <h2 className="tasks-title">任务</h2>
        {runs.length === 0 ? (
          <p className="tasks-empty"><span>暂无任务。</span><span>评估、定制简历、公司研究等任务开始后，会在这里显示进度。</span></p>
        ) : (
          <TaskGroups runs={runs} close={() => panel.current?.hidePopover()} />
        )}
      </div>
    </>
  )
}

function TaskGroups({ runs, close }: { runs: Run[]; close: () => void }) {
  // 运行只记了岗位编号，岗位名逐个去取（和岗位详情共用缓存）
  const ids = [...new Set(runs.flatMap((r) => r.job_ids))]
  const jobs = useQueries({
    queries: ids.map((jobId) => ({ queryKey: ['jobs', 'detail', jobId], queryFn: () => api<Job>(`/jobs/${jobId}`), retry: false })),
  })
  const names = new Map(ids.map((jobId, i) => {
    const job = jobs[i].data
    return [jobId, job ? job.company || job.title || '未命名岗位' : jobs[i].isError ? '已删除的岗位' : '…']
  }))
  return (
    <div className="tasks-body">
      {GROUPS.map((g) => {
        const list = runs.filter(g.match)
        if (!list.length) return null
        return (
          <section key={g.label} className="tasks-group">
            <h3>{g.label}<span>{list.length}</span></h3>
            <ul>{list.map((r) => <TaskRow key={r.id} run={r} names={names} close={close} />)}</ul>
          </section>
        )
      })}
    </div>
  )
}

function TaskRow({ run, names, close }: { run: Run; names: Map<number, string>; close: () => void }) {
  const navigate = useNavigate()
  const toast = useToast()
  const cancel = useRunAction('cancel')
  const resume = useRunAction('resume')
  const target = run.job_ids.length === 1 ? names.get(run.job_ids[0]) : run.job_ids.length > 1 ? `${run.job_ids.length} 个岗位` : ''
  const open = () => {
    close()
    navigate(runLink(run))
  }
  // 取消的结果由运行结束时的提示告诉你，这里只在出错时提示
  const act = (mutation: typeof cancel, done?: string) => mutation.mutate(run.id, {
    onSuccess: () => { if (done) toast(done) },
    onError: (e) => toast(e.message, 'error'),
  })
  // 多项的运行（批量评估、采集）用进度条表示做完几项；「已完成 x/y」这句就不再重复
  const counted = run.status === 'running' && run.total > 1 && run.done !== null
  const detail = run.status === 'running' ? (counted && (!run.step || run.step.startsWith('已完成')) ? '' : run.step || '准备中')
    : run.status === 'waiting_user' ? (run.kind === 'tailoring' ? '修改方案等你确认' : '等你回答')
      : run.status === 'cancelled' ? '已取消'
        : run.status === 'failed' ? run.error ?? '没有完成'
          : '完成'
  return (
    <li className="task" data-status={run.status}>
      <div className="task-main">
        <strong>{RUN_KINDS[run.kind]}{target && <span> · {target}</span>}</strong>
        {detail && <p title={detail}>{detail}</p>}
        {counted && <ProgressBar done={run.done!} total={run.total} />}
        <small>{fmtTime(run.created_at)}</small>
      </div>
      <div className="task-actions">
        {run.status === 'running' && <Button size="sm" onClick={() => act(cancel)} disabled={cancel.isPending}>取消</Button>}
        {/* 识别截图没完成时重新带上截图添加更直接，不提供从断点继续 */}
        {(run.status === 'failed' || run.status === 'cancelled') && run.kind !== 'screenshot' && (
          <Button size="sm" onClick={() => act(resume, '已从断点继续')} disabled={resume.isPending} title="已完成的步骤不会重做">从断点继续</Button>
        )}
        {(run.job_ids.length <= 1 || run.kind === 'profile') && <Button size="sm" onClick={open}>{run.status === 'waiting_user' ? '去处理' : '打开'}</Button>}
        {(run.status === 'failed' || run.status === 'cancelled') && <CloseNotice run={run} />}
      </div>
    </li>
  )
}
