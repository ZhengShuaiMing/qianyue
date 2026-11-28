import { useRun, useRunAction, type Run } from '../../api/runs'
import type { JobProgress } from '../../app/runs/runs-context'
import { fmtTime } from '../../lib/format'
import { Button } from '../../ui/Button'
import { Group } from '../../ui/Group'
import { IconClose } from '../../ui/icons'
import '../../ui/icon-button.css'
import { dismissRun, useDismissed } from '../../app/runs/dismissed'
import { useToast } from '../../ui/toast'

/** 正在进行：已经走过的步骤，当前这步下面列出 Agent 用过的工具（搜了什么、读了哪个网页） */
export function RunProgress({ run, progress, title }: { run: Run; progress: JobProgress | undefined; title: string }) {
  const cancel = useRunAction('cancel')
  const toast = useToast()
  const labels = progress?.labels ?? []
  return (
    <Group
      label={title}
      action={<Button size="sm" onClick={() => cancel.mutate(run.id, { onError: (e) => toast(e.message, 'error') })} disabled={cancel.isPending}>{cancel.isPending ? '正在取消…' : '取消'}</Button>}
    >
      <div aria-live="polite">
        {labels.length === 0 && <div className="eval-step" data-state="now"><span className="eval-step-dot" />准备中</div>}
        {labels.map((label, i) => {
          const now = i === labels.length - 1
          return (
            <div key={label}>
              <div className="eval-step" data-state={now ? 'now' : 'done'}>
                <span className="eval-step-dot" />{label}
                <span className="eval-step-state">{now ? '进行中' : '完成'}</span>
              </div>
              {now && progress && progress.tools.length > 0 && (
                <ol className="run-tools">
                  {progress.tools.map((t, j) => <li key={j}><span>{t.label}</span>{t.detail}</li>)}
                </ol>
              )}
            </div>
          )
        })}
      </div>
    </Group>
  )
}

/** 上一次没做完：说原因，能从断点继续。批量运行里这个岗位自己做完了就不提 */
export function RunFailed({ run, jobId, what }: { run: Run; jobId: number; what: string }) {
  const resume = useRunAction('resume')
  const toast = useToast()
  const detail = useRun(run.id)
  const dismissed = useDismissed()
  const mine = detail.data?.results.find((r) => r.job_id === jobId)
  if (!detail.data || dismissed(run) || (mine && ['done', 'reused', 'no_changes'].includes(mine.status))) return null
  const cancelled = run.status === 'cancelled'
  const reason = mine?.reason ?? (run.job_ids.length > 1 ? '这个岗位还没做到' : run.error)
  return (
    <section className="eval-failed" data-cancelled={cancelled}>
      <div>
        <strong>{cancelled ? `上次${what}取消了` : `上次${what}没有完成`}</strong>
        <span>{cancelled ? `${fmtTime(run.updated_at)} 取消` : reason}</span>
      </div>
      <Button size="sm" onClick={() => resume.mutate(run.id, { onError: (e) => toast(e.message, 'error') })} disabled={resume.isPending} title="已完成的步骤不会重做">
        {resume.isPending ? '正在继续…' : '从断点继续'}
      </Button>
      <CloseNotice run={run} />
    </section>
  )
}

/** 上次做完了但没有产出：定制时一条都没接受、模型认为不用改；模拟面试一题都没答 */
export function LastOutcome({ run, jobId, title }: { run: Run; jobId: number; title: string }) {
  const detail = useRun(run.id)
  const dismissed = useDismissed()
  const mine = detail.data?.results.find((r) => r.job_id === jobId)
  if (!mine || dismissed(run) || !['declined', 'no_changes', 'empty'].includes(mine.status)) return null
  return <section className="eval-failed" data-cancelled="true"><div><strong>{title}</strong><span>{mine.reason}</span></div><CloseNotice run={run} /></section>
}

/** 提示右边的 ×：关掉以后这次运行不再提示（任务面板里也不再列出） */
export function CloseNotice({ run }: { run: Run }) {
  return <button type="button" className="notice-close" aria-label="关闭这条提示" title="关闭这条提示" onClick={() => dismissRun(run)}><IconClose /></button>
}
