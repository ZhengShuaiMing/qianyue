import { useState } from 'react'
import { useArtifact, useLatestArtifact, type ArtifactKind } from '../../api/artifacts'
import type { Job, JobStage } from '../../api/jobs'
import { useRun, useStartRun, type RunKind } from '../../api/runs'
import { useRunsLive } from '../../app/runs/runs-context'
import { ArtifactBar } from '../../reports/ArtifactBar'
import { DocView } from '../../reports/DocView'
import { Button } from '../../ui/Button'
import { Dialog } from '../../ui/Dialog'
import { EmptyState } from '../../ui/EmptyState'
import { Field, TextInput } from '../../ui/Field'
import { useToast } from '../../ui/toast'
import { STAGES } from './job-look'
import { RunFailed, RunProgress } from './RunViews'
import { StageDialog } from './StageDialog'

export type DocFeatureCopy = {
  /** 这件事叫什么：「公司研究」 */
  what: string
  /** 进行中的标题：「正在研究这家公司」 */
  running: string
  emptyTitle: string
  emptyText: string
  action: string
  redo: string
  hint: string
}

/** 公司研究、沟通建议、面试准备共用：没做过 → 填条件发起；进行中 → 步骤和工具；做完 → 报告，可重新生成 */
export function DocFeature({ job, runKind, artifactKind, copy }: { job: Job; runKind: RunKind; artifactKind: ArtifactKind; copy: DocFeatureCopy }) {
  const live = useRunsLive()
  const running = live.activeRun(job.id, runKind)
  const latest = live.latestRun(job.id, runKind)
  const artifact = useLatestArtifact(job.id, artifactKind)

  if (running) return <RunProgress run={running} progress={live.progress(job.id, running.id)} title={copy.running} />
  const failed = latest && (latest.status === 'failed' || latest.status === 'cancelled') ? latest : undefined
  return (
    <>
      {failed && <RunFailed run={failed} jobId={job.id} what={copy.what} />}
      {artifact.isError ? (
        <EmptyState title={`${copy.what}没有读出来`} action={<Button onClick={() => artifact.refetch()}>再试一次</Button>}>{artifact.error.message}</EmptyState>
      ) : artifact.isPending ? (
        <p className="job-loading">正在读取…</p>
      ) : artifact.data ? (
        <DocBody job={job} artifactId={artifact.data.id} runKind={runKind} copy={copy} />
      ) : (
        <StartBox job={job} runKind={runKind} copy={copy} />
      )}
    </>
  )
}

function StartBox({ job, runKind, copy }: { job: Job; runKind: RunKind; copy: DocFeatureCopy }) {
  const [conditions, setConditions] = useState('')
  const start = useStartRun()
  const toast = useToast()
  const go = () => start.mutate(
    { kind: runKind, job_ids: [job.id], conditions: conditions.trim() },
    { onError: (e) => toast(`没有开始${copy.what}：${e.message}`, 'error') },
  )
  return (
    <section className="eval-start">
      <p className="eval-start-title">{copy.emptyTitle}</p>
      <p className="eval-start-text">{copy.emptyText}</p>
      <form className="eval-start-row" onSubmit={(e) => { e.preventDefault(); go() }}>
        <label className="eval-cond">
          <input value={conditions} onChange={(e) => setConditions(e.target.value)} placeholder={copy.hint} aria-label={`${copy.what}的要求`} />
        </label>
        <Button type="submit" variant="primary" disabled={start.isPending}>{start.isPending ? '正在开始…' : copy.action}</Button>
      </form>
    </section>
  )
}

function DocBody({ job, artifactId, runKind, copy }: { job: Job; artifactId: number; runKind: RunKind; copy: DocFeatureCopy }) {
  const artifact = useArtifact(artifactId)
  const [redo, setRedo] = useState(false)
  if (artifact.isError) return <EmptyState title={`${copy.what}没有读出来`} action={<Button onClick={() => artifact.refetch()}>再试一次</Button>}>{artifact.error.message}</EmptyState>
  if (!artifact.data) return <p className="job-loading">正在读取…</p>
  return (
    <>
      <ArtifactBar artifact={artifact.data} redo={{ label: copy.redo, onClick: () => setRedo(true) }} />
      {runKind === 'communication' && <StageSuggestion job={job} data={artifact.data.data} />}
      <DocView md={artifact.data.content_md} />
      {redo && <RedoDialog job={job} runKind={runKind} runId={artifact.data.run_id} copy={copy} onClose={() => setRedo(false)} />}
    </>
  )
}

/** 重新生成：要求默认沿用生成这份时的，输入没变也重新做 */
function RedoDialog({ job, runKind, runId, copy, onClose }: { job: Job; runKind: RunKind; runId: string | null; copy: DocFeatureCopy; onClose: () => void }) {
  const last = useRun(runId)
  const [value, setValue] = useState<string | null>(null)
  const conditions = value ?? last.data?.conditions ?? ''
  const start = useStartRun()
  const submit = () => {
    if (start.isPending) return
    start.mutate({ kind: runKind, job_ids: [job.id], conditions: conditions.trim(), force: true }, { onSuccess: onClose })
  }
  return (
    <Dialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={copy.redo}
      actions={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button type="submit" variant="primary" disabled={start.isPending}>{start.isPending ? '正在开始…' : copy.redo}</Button>
        </>
      }
    >
      <p className="dialog-text">按现在的岗位信息、你的材料和下面的要求重新生成。这些内容都和上次相同时，新结果替换这一份；有任何变化时另存一份，这里显示最新的，旧的仍在「成果」页。</p>
      <Field label="要求" hint="选填">
        <TextInput value={conditions} onChange={(e) => setValue(e.target.value)} placeholder={copy.hint} autoFocus />
      </Field>
      {start.isError && <p className="dialog-text" data-tone="error">没有开始：{start.error.message}</p>}
    </Dialog>
  )
}

/** 沟通建议认为岗位可能进入了新阶段：给一个按钮，打开改阶段并选好建议的阶段，仍要你确认 */
function StageSuggestion({ job, data }: { job: Job; data: Record<string, unknown> | null }) {
  const [open, setOpen] = useState(false)
  const output = (data?.output ?? {}) as { suggested_job_stage?: JobStage | null; stage_reason?: string }
  const suggested = output.suggested_job_stage
  if (!suggested || suggested === job.stage) return null
  return (
    <section className="stage-suggest">
      <div>
        <strong>对话显示这个岗位可能已经到了「{STAGES[suggested]}」</strong>
        {output.stage_reason && <span>{output.stage_reason}</span>}
      </div>
      <Button size="sm" onClick={() => setOpen(true)}>改为「{STAGES[suggested]}」</Button>
      {open && <StageDialog job={job} open suggested={suggested} onClose={() => setOpen(false)} />}
    </section>
  )
}
