import { useState } from 'react'
import { useArtifact, useLatestArtifact, type Artifact } from '../../api/artifacts'
import type { Job } from '../../api/jobs'
import { useRespondRun, useRun, useStartRun, useTailoringPreview, type Overrides, type Run } from '../../api/runs'
import { useRunsLive } from '../../app/runs/runs-context'
import { ArtifactBar } from '../../reports/ArtifactBar'
import { Md } from '../../reports/Md'
import { Button } from '../../ui/Button'
import { ConfirmDialog } from '../../ui/ConfirmDialog'
import { EmptyState } from '../../ui/EmptyState'
import { Group } from '../../ui/Group'
import { IconCheck } from '../../ui/icons'
import { useToast } from '../../ui/toast'
import { EditCompare } from './EditCompare'
import { LastOutcome, RunFailed, RunProgress } from './RunViews'

// 修改方案（运行停在「等你确认」时的 pending），字段来自 backend/app/graphs/tailoring.py 的 build_proposal
type Edit = {
  number: number
  kind: 'replace' | 'delete' | 'insert' | 'move'
  start: number | null
  end: number | null
  after: number | null
  new_text: string
  /** same_facts 只改说法；from_evidence 用了材料里的事实；needs_confirmation 要你确认 */
  fact_check: 'same_facts' | 'from_evidence' | 'needs_confirmation' | null
  jd_basis: string
  reason: string
  original: string
  evidence: { id: string; source: string; text: string }[]
  /** 新内容里在简历和证据中都找不到出处的数字、技术词 */
  unverified: string[]
  /** 措辞核对的提醒：新说法超出了原文 */
  concerns: string[]
}
type Proposal = { summary: string; edits: Edit[]; gaps: string[]; questions: string[] }

const KIND: Record<Edit['kind'], string> = { replace: '替换', delete: '删除', insert: '插入', move: '移动' }
const FACT: Record<NonNullable<Edit['fact_check']>, string> = {
  same_facts: '只改说法，事实不变',
  from_evidence: '用了你材料里的事实',
  needs_confirmation: '需要你确认',
}
const where = (e: Edit) =>
  e.kind === 'insert' ? `插在第 ${e.after} 行后` : e.kind === 'move' ? `第 ${e.start}–${e.end} 行移到第 ${e.after} 行后`
    : e.start === e.end ? `第 ${e.start} 行` : `第 ${e.start}–${e.end} 行`
const without = (all: Overrides, n: number): Overrides => Object.fromEntries(Object.entries(all).filter(([k]) => Number(k) !== n))
const risky = (e: Edit) => e.fact_check === 'needs_confirmation' || e.unverified.length > 0 || e.concerns.length > 0

/** 定制简历：发起 → 生成修改方案 → 你逐条勾选 → 只应用勾选的，生成岗位版简历。基础简历不变 */
export function Tailoring({ job }: { job: Job }) {
  const live = useRunsLive()
  const active = live.activeRun(job.id, 'tailoring')
  const latest = live.latestRun(job.id, 'tailoring')
  const artifact = useLatestArtifact(job.id, 'tailored_resume')

  if (active?.status === 'waiting_user') return <Review run={active} />
  if (active) return <RunProgress run={active} progress={live.progress(job.id, active.id)} title="正在生成修改方案" />
  const failed = latest && (latest.status === 'failed' || latest.status === 'cancelled') ? latest : undefined
  return (
    <>
      {failed && <RunFailed run={failed} jobId={job.id} what="定制" />}
      {latest?.status === 'succeeded' && !artifact.data && <LastOutcome run={latest} jobId={job.id} title="上次定制没有生成简历" />}
      {artifact.data ? <Result job={job} artifactId={artifact.data.id} /> : !artifact.isPending && <Start job={job} />}
    </>
  )
}

function Start({ job, again }: { job: Job; again?: boolean }) {
  const start = useStartRun()
  const toast = useToast()
  const go = () => start.mutate({ kind: 'tailoring', job_ids: [job.id], force: again }, { onError: (e) => toast(`没有开始定制：${e.message}`, 'error') })
  if (again) return <Button size="sm" onClick={go} disabled={start.isPending}>{start.isPending ? '正在开始…' : '重新定制'}</Button>
  return (
    <section className="eval-start">
      <p className="eval-start-title">还没有定制简历</p>
      <div className="eval-start-row eval-start-solo">
        <p className="eval-start-text">对照 JD 从你的材料里找真实经历，给出逐条修改。你确认后才生成定制简历，基础简历不变。</p>
        <Button variant="primary" onClick={go} disabled={start.isPending}>{start.isPending ? '正在开始…' : '开始定制'}</Button>
      </div>
    </section>
  )
}

/** 逐条确认。默认勾上不需要你确认、也没有措辞提醒的修改 */
function Review({ run }: { run: Run }) {
  const detail = useRun(run.id)
  const proposal = detail.data?.pending as Proposal | null | undefined
  if (detail.isError) return <EmptyState title="修改方案没有读出来" action={<Button onClick={() => detail.refetch()}>再试一次</Button>}>{detail.error.message}</EmptyState>
  if (!proposal) return <p className="job-loading">正在读取修改方案…</p>
  return <ReviewForm run={run} proposal={proposal} />
}

function ReviewForm({ run, proposal }: { run: Run; proposal: Proposal }) {
  const [accepted, setAccepted] = useState(() => new Set(proposal.edits.filter((e) => !risky(e)).map((e) => e.number)))
  const [discarding, setDiscarding] = useState(false)
  const [preview, setPreview] = useState(false)
  // 你改过的新内容。取消勾选时先留着，再勾上还在；提交和预览只带勾选了的
  const [edits, setEdits] = useState<Overrides>({})
  const [editing, setEditing] = useState<number | null>(null)
  const respond = useRespondRun()
  const toast = useToast()
  const toggle = (n: number) => setAccepted((s) => { const next = new Set(s); if (next.has(n)) next.delete(n); else next.add(n); return next })
  const rewrite = (e: Edit, text: string) => {
    setEdits((all) => (text === e.new_text ? without(all, e.number) : { ...all, [e.number]: text }))
    setAccepted((s) => new Set(s).add(e.number)) // 改过就是想要这条
    setEditing(null)
  }
  const restore = (n: number) => setEdits((all) => without(all, n))
  const chosen: Overrides = Object.fromEntries(Object.entries(edits).filter(([n]) => accepted.has(Number(n))))
  const submit = (numbers: number[]) => respond.mutate(
    { id: run.id, accepted: numbers, overrides: numbers.length ? chosen : {} },
    {
      onSuccess: () => { toast(numbers.length ? `正在应用 ${numbers.length} 条修改` : '已放弃这次定制'); setDiscarding(false) },
      onError: (e) => toast(`没有提交：${e.message}`, 'error'),
    },
  )
  return (
    <>
      <section className="tailor-wait">
        <span className="tailor-wait-dot" />
        <div><strong>等你确认 · {proposal.edits.length} 条修改</strong><span>只会应用你勾选的修改，基础简历不变</span></div>
      </section>
      <p className="report-summary">{proposal.summary}</p>

      <div className="tailor-edits">
        {proposal.edits.map((e) => {
          const on = accepted.has(e.number)
          const mine = edits[e.number]
          return (
            <article key={e.number} className="tailor-edit" data-on={on}>
              <header>
                <button type="button" className="tailor-check" role="checkbox" aria-checked={on} aria-label={`接受修改 ${e.number}`} onClick={() => toggle(e.number)}>
                  <IconCheck />
                </button>
                <strong>修改 {e.number}</strong>
                <span className="tailor-pill">{KIND[e.kind]} · {where(e)}</span>
                {e.fact_check && <span className="tailor-pill" data-tone={e.fact_check === 'needs_confirmation' ? 'warn' : undefined}>{FACT[e.fact_check]}</span>}
                {e.reason.startsWith('措辞升级') && <span className="tailor-pill" data-tone="warn">措辞升级</span>}
                {mine !== undefined && <span className="tailor-pill" data-tone="mine">你改过</span>}
              </header>
              <p className="tailor-reason">{e.reason}</p>
              <EditCompare
                kind={e.kind}
                original={e.original}
                text={mine ?? e.new_text}
                edited={mine !== undefined}
                editing={editing === e.number}
                onEdit={() => setEditing(e.number)}
                onDone={(text) => rewrite(e, text)}
                onCancel={() => setEditing(null)}
                onRestore={() => restore(e.number)}
              />
              <p className="tailor-basis"><span>对应 JD</span>{e.jd_basis}</p>
              {mine !== undefined ? (
                <p className="tailor-basis">这条将按你填写的内容原样使用，不再与材料核对。</p>
              ) : (e.unverified.length > 0 || e.concerns.length > 0) && (
                <ul className="tailor-warns">
                  {e.unverified.length > 0 && <li>这些说法在你的材料里找不到出处：{e.unverified.join('、')}</li>}
                  {e.concerns.map((c) => <li key={c}>{c}</li>)}
                </ul>
              )}
              {e.evidence.length > 0 && (
                <details className="tailor-evidence">
                  <summary>依据的材料 {e.evidence.length} 段</summary>
                  {e.evidence.map((ev) => <blockquote key={ev.id}><small>{ev.source}</small>{ev.text}</blockquote>)}
                </details>
              )}
            </article>
          )
        })}
      </div>

      {proposal.gaps.length > 0 && <Group label="简历里补不上的缺口" hint="不会为了贴合 JD 编造经历"><div className="prose"><ul>{proposal.gaps.map((g) => <li key={g}>{g}</li>)}</ul></div></Group>}
      {proposal.questions.length > 0 && (
        <Group label="需要你补充的信息" hint="如果有这些经历，请写进「材料」页的基础简历或 STAR 经历，下次定制时会用到">
          <div className="prose"><ul>{proposal.questions.map((q) => <li key={q}>{q}</li>)}</ul></div>
        </Group>
      )}
      <Group label="应用勾选的修改后" hint={`按当前勾选的 ${accepted.size} 条生成，勾选变化后预览会随之更新`} action={<Button size="sm" onClick={() => setPreview((v) => !v)}>{preview ? '收起' : '展开预览'}</Button>}>
        {preview ? <Preview runId={run.id} accepted={[...accepted]} overrides={chosen} /> : <p className="tailor-preview-hint">点「展开预览」看整份简历</p>}
      </Group>

      <div className="tailor-bar float">
        <span>已选 {accepted.size} / {proposal.edits.length} 条</span>
        <Button size="sm" onClick={() => setDiscarding(true)} disabled={respond.isPending}>放弃这次定制</Button>
        <Button size="sm" variant="primary" onClick={() => submit([...accepted].sort((a, b) => a - b))} disabled={accepted.size === 0 || editing !== null || respond.isPending} title={editing !== null ? '先点「完成」或「取消」结束正在改的那条' : undefined}>
          {respond.isPending ? '正在提交…' : `应用 ${accepted.size} 条，生成定制简历`}
        </Button>
      </div>
      <ConfirmDialog
        open={discarding}
        title="放弃这次定制？"
        confirmLabel="放弃"
        busy={respond.isPending}
        error={null}
        onConfirm={() => submit([])}
        onClose={() => setDiscarding(false)}
      >
        这份修改方案不会再用，不生成定制简历。以后想要可以重新定制，会重新调用模型。
      </ConfirmDialog>
    </>
  )
}

function Preview({ runId, accepted, overrides }: { runId: string; accepted: number[]; overrides: Overrides }) {
  const preview = useTailoringPreview(runId, accepted, overrides)
  if (preview.isError) return <p className="tailor-preview-hint">预览没有算出来：{preview.error.message}</p>
  if (!preview.data) return <p className="tailor-preview-hint">正在计算…</p>
  return <div className="prose" data-stale={preview.isPlaceholderData}><Md>{preview.data.preview_md}</Md></div>
}

/** 生成好的岗位版简历，加上这次接受了哪几条修改 */
function Result({ job, artifactId }: { job: Job; artifactId: number }) {
  const artifact = useArtifact(artifactId)
  if (artifact.isError) return <EmptyState title="定制简历没有读出来" action={<Button onClick={() => artifact.refetch()}>再试一次</Button>}>{artifact.error.message}</EmptyState>
  if (!artifact.data) return <p className="job-loading">正在读取…</p>
  const a: Artifact = artifact.data
  const data = a.data as { proposal: Proposal; accepted: number[]; overrides: Record<string, string> }
  const used = data.proposal.edits.filter((e) => data.accepted.includes(e.number))
  return (
    <>
      <ArtifactBar artifact={a} />
      <div className="tailor-result-head">
        <p>接受了 {used.length} / {data.proposal.edits.length} 条修改。基础简历没有改动，这是只给这个岗位用的一份。</p>
        <Start job={job} again />
      </div>
      <Group label="定制简历"><div className="prose"><Md>{a.content_md}</Md></div></Group>
      <Group label="这次接受的修改">
        {used.map((e) => (
          <div key={e.number} className="tailor-used">
            <strong>修改 {e.number} · {KIND[e.kind]} · {where(e)}{data.overrides[e.number] !== undefined && ' · 你改过'}</strong>
            <p>{e.reason}</p>
          </div>
        ))}
      </Group>
    </>
  )
}
