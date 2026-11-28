import { useState } from 'react'
import type { Job } from '../../api/jobs'
import { useArtifact } from '../../api/artifacts'
import { useRunAction, useStartRun, type Run } from '../../api/runs'
import { useRunsLive, type JobProgress } from '../../app/runs/runs-context'
import { ArtifactBar } from '../../reports/ArtifactBar'
import type { EvaluationData } from '../../reports/evaluation-data'
import { EvaluationReport } from '../../reports/EvaluationReport'
import { Button } from '../../ui/Button'
import { Dialog } from '../../ui/Dialog'
import { Field, TextInput } from '../../ui/Field'
import { EmptyState } from '../../ui/EmptyState'
import { Group } from '../../ui/Group'
import { useToast } from '../../ui/toast'
import { STEPS } from './eval-steps'
import { RunFailed } from './RunViews'

/** 评估页：JD 不完整 / 正在评估 / 上次没评完 / 还没评估 / 已评估 */
export function EvaluationTab({ job, onAddJd }: { job: Job; onAddJd: () => void }) {
  const live = useRunsLive()
  const running = live.activeRun(job.id, 'evaluation')
  const latest = live.latestRun(job.id, 'evaluation')

  if (!job.has_jd) {
    return (
      <EmptyState title="JD 还不完整" action={<Button variant="primary" onClick={onAddJd}>补充 JD</Button>}>
        评估要读职位描述。补上 JD 后岗位会变成「待评估」。
      </EmptyState>
    )
  }
  if (running) return <EvalRunning run={running} progress={live.progress(job.id, running.id)} />

  const failed = latest && (latest.status === 'failed' || latest.status === 'cancelled') ? latest : undefined
  return (
    <>
      {failed && <RunFailed run={failed} jobId={job.id} what="评估" />}
      {job.evaluation ? <EvalReport job={job} artifactId={job.evaluation.artifact_id} /> : <EvalStart job={job} />}
    </>
  )
}

/** 已评估：最近一份报告。重新评估时可以改条件，默认沿用上次的 */
function EvalReport({ job, artifactId }: { job: Job; artifactId: number }) {
  const artifact = useArtifact(artifactId)
  const [redo, setRedo] = useState(false)
  if (artifact.isError) {
    return <EmptyState title="评估报告没有读出来" action={<Button onClick={() => artifact.refetch()}>再试一次</Button>}>{artifact.error.message}</EmptyState>
  }
  if (!artifact.data) return <p className="job-loading">正在读取报告…</p>
  const last = (artifact.data.data as EvaluationData).inputs.conditions
  return (
    <>
      <ArtifactBar artifact={artifact.data} redo={{ label: '重新评估', onClick: () => setRedo(true) }} />
      {last && <p className="eval-conditions">这次的条件：{last}</p>}
      <EvaluationReport artifact={artifact.data} />
      {redo && <RedoDialog job={job} conditions={last} onClose={() => setRedo(false)} />}
    </>
  )
}

/** 发起评估：一句选填的条件 + 开始按钮 */
function EvalStart({ job }: { job: Job }) {
  const [conditions, setConditions] = useState('')
  const start = useStartRun()
  const toast = useToast()
  const go = () => start.mutate(
    { kind: 'evaluation', job_ids: [job.id], conditions: conditions.trim() },
    { onError: (e) => toast(`没有开始评估：${e.message}`, 'error') },
  )
  return (
    <section className="eval-start">
      <p className="eval-start-title">还没有评估</p>
      <p className="eval-start-text">
        对照你的材料，从八个维度为这个岗位评分。关闭页面后，评估会在后台继续。
      </p>
      <form className="eval-start-row" onSubmit={(e) => { e.preventDefault(); go() }}>
        <label className="eval-cond">
          <input value={conditions} onChange={(e) => setConditions(e.target.value)} placeholder="这次评估更看重什么，比如技术成长空间、想做业务落地而不是纯研究（选填）" aria-label="评估条件" />
        </label>
        <Button type="submit" variant="primary" disabled={start.isPending}>{start.isPending ? '正在开始…' : '开始评估'}</Button>
      </form>
    </section>
  )
}

/** 重新评估：按现在的 JD、材料和条件再评一次，输入没变也重新调用模型 */
function RedoDialog({ job, conditions, onClose }: { job: Job; conditions: string; onClose: () => void }) {
  const [value, setValue] = useState(conditions)
  const start = useStartRun()
  const submit = () => {
    if (start.isPending) return
    start.mutate({ kind: 'evaluation', job_ids: [job.id], conditions: value.trim(), force: true }, { onSuccess: onClose })
  }
  return (
    <Dialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="重新评估"
      actions={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button type="submit" variant="primary" disabled={start.isPending}>{start.isPending ? '正在开始…' : '重新评估'}</Button>
        </>
      }
    >
      <p className="dialog-text">按现在的 JD、你的材料和下面的条件再评一次。JD、材料和条件都和上次一样时，新报告替换这份；有变化时另存一份，岗位上显示新的。</p>
      <Field label="条件" hint="选填">
        <TextInput value={value} onChange={(e) => setValue(e.target.value)} placeholder="比如只考虑上海、月薪不低于 30K" autoFocus />
      </Field>
      {start.isError && <p className="dialog-text" data-tone="error">没有开始：{start.error.message}</p>}
    </Dialog>
  )
}

function EvalRunning({ run, progress }: { run: Run; progress: JobProgress | undefined }) {
  const cancel = useRunAction('cancel')
  const toast = useToast()
  const seen = progress?.seen ?? []
  const current = seen[seen.length - 1]
  const steps = STEPS.filter((s) => !s.optional || seen.includes(s.node))
  return (
    <Group
      label={run.job_ids.length > 1 ? `正在评估 · 和另外 ${run.job_ids.length - 1} 个岗位一起` : '正在评估'}
      action={<Button size="sm" onClick={() => cancel.mutate(run.id, { onError: (e) => toast(e.message, 'error') })} disabled={cancel.isPending}>{cancel.isPending ? '正在取消…' : '取消'}</Button>}
    >
      <div aria-live="polite">
        {steps.map((s) => {
          const state = s.node === current ? 'now' : seen.includes(s.node) ? 'done' : 'todo'
          return (
            <div key={s.node} className="eval-step" data-state={state}>
              <span className="eval-step-dot" />{s.node === 'recheck' ? '复评（总分接近分数线，再评两次，每个维度取三次的中间值）' : s.label}
              <span className="eval-step-state">{state === 'done' ? '完成' : state === 'now' ? '进行中' : ''}</span>
            </div>
          )
        })}
      </div>
    </Group>
  )
}
