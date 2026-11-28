import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useArtifact, useLatestArtifact } from '../../api/artifacts'
import type { Job } from '../../api/jobs'
import { useStartRun, type Run } from '../../api/runs'
import { useRunsLive } from '../../app/runs/runs-context'
import { ArtifactBar } from '../../reports/ArtifactBar'
import { DocView } from '../../reports/DocView'
import { Button } from '../../ui/Button'
import { ConfirmDialog } from '../../ui/ConfirmDialog'
import { TextArea } from '../../ui/Field'
import { Group } from '../../ui/Group'
import { useToast } from '../../ui/toast'
import { LastOutcome, RunFailed } from './RunViews'

type Turn = { question: string; answer: string | null }

/** 模拟面试：没在面 → 发起（可以写想练什么），下面是上一场的复盘；在面 → 面试间 */
export function MockInterview({ job }: { job: Job }) {
  const live = useRunsLive()
  const active = live.activeRun(job.id, 'mock_interview')
  const latest = live.latestRun(job.id, 'mock_interview')
  const review = useLatestArtifact(job.id, 'mock_interview')
  if (active) return <Room run={active} />
  const failed = latest && (latest.status === 'failed' || latest.status === 'cancelled') ? latest : undefined
  return (
    <>
      {failed && <RunFailed run={failed} jobId={job.id} what="模拟面试" />}
      {latest?.status === 'succeeded' && <LastOutcome run={latest} jobId={job.id} title="上一场没有生成复盘" />}
      <Start job={job} again={!!review.data} />
      {review.data && <LastReview artifactId={review.data.id} />}
    </>
  )
}

function Start({ job, again }: { job: Job; again: boolean }) {
  const [conditions, setConditions] = useState('')
  const start = useStartRun()
  const toast = useToast()
  const go = () => start.mutate(
    { kind: 'mock_interview', job_ids: [job.id], conditions: conditions.trim(), force: again },
    { onError: (e) => toast(`没有开始模拟面试：${e.message}`, 'error') },
  )
  return (
    <section className={again ? 'eval-start eval-start-again' : 'eval-start'}>
      {!again && <p className="eval-start-title">还没有模拟过</p>}
      <p className="eval-start-text">AI 面试官一问一答，最多 8 轮，回答含糊会追问；可以随时结束，结束后逐轮复盘。中途关闭页面后，回来可以继续这场面试。</p>
      <form className="eval-start-row" onSubmit={(e) => { e.preventDefault(); go() }}>
        <label className="eval-cond">
          <input value={conditions} onChange={(e) => setConditions(e.target.value)} placeholder="想让面试官扮演谁、重点练什么（选填）" aria-label="模拟面试的要求" />
        </label>
        <Button type="submit" variant={again ? 'glass' : 'primary'} disabled={start.isPending}>
          {start.isPending ? '正在开始…' : again ? '开始新一场模拟面试' : '开始模拟面试'}
        </Button>
      </form>
    </section>
  )
}

function LastReview({ artifactId }: { artifactId: number }) {
  const artifact = useArtifact(artifactId)
  if (!artifact.data) return null
  return (
    <div className="mock-review">
      <h3 className="mock-review-title">上一场的复盘</h3>
      <ArtifactBar artifact={artifact.data} />
      <DocView md={artifact.data.content_md} />
    </div>
  )
}

/** 面试间：连上这场面试的 WebSocket。面试官的话逐段显示；轮到你时在下面作答 */
function Room({ run }: { run: Run }) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [asking, setAsking] = useState('')
  const [round, setRound] = useState<{ turn: number; total: number } | null>(null)
  const [status, setStatus] = useState('正在连接')
  const [waiting, setWaiting] = useState(false)
  const [answer, setAnswer] = useState('')
  const [problem, setProblem] = useState('')
  const [ending, setEnding] = useState(false)
  const socket = useRef<WebSocket | null>(null)
  const bottom = useRef<HTMLDivElement>(null)
  const client = useQueryClient()
  const toast = useToast()

  useEffect(() => {
    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${scheme}://${window.location.host}/api/runs/${run.id}/interview`)
    socket.current = ws
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data)
      if (msg.type === 'status') {
        setStatus(msg.label)
        if (msg.label === '面试官提问') setAsking('')
      } else if (msg.type === 'token') {
        setWaiting(false)
        setAsking((text) => text + msg.text)
      } else if (msg.type === 'question') {
        // 每轮问完都带上完整的问题和此前的问答：刷新、断线重连后也能接上
        setTurns([...msg.transcript, { question: msg.question, answer: null }])
        setAsking('')
        setRound({ turn: msg.turn, total: msg.total })
        setWaiting(true)
        setStatus('等你回答')
      } else if (msg.type === 'error') {
        setProblem(msg.message)
      } else if (msg.type === 'done') {
        void client.invalidateQueries({ queryKey: ['runs'] })
        void client.invalidateQueries({ queryKey: ['artifacts', 'list'] })
        if (msg.status === 'failed') toast(`模拟面试没有完成：${msg.error ?? '原因不明'}`, 'error')
      }
    }
    ws.onclose = () => setWaiting(false)
    return () => { ws.onmessage = null; ws.onclose = null; ws.close() }
  }, [run.id, client, toast])

  useEffect(() => { bottom.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) }, [turns.length, waiting])

  const send = (message: { type: 'answer'; text: string } | { type: 'end' }) => {
    if (socket.current?.readyState !== WebSocket.OPEN) return setProblem('与模拟面试的连接已断开，刷新页面后可从这一题继续')
    socket.current.send(JSON.stringify(message))
    setProblem('')
    setWaiting(false)
    if (message.type === 'answer') {
      setTurns((list) => list.map((t, i) => (i === list.length - 1 ? { ...t, answer: message.text } : t)))
      setAnswer('')
    }
  }
  const submit = () => { if (answer.trim() && waiting) send({ type: 'answer', text: answer.trim() }) }

  const thinking = !waiting && !asking
  return (
    <section className="mock-room">
      <Group label={round ? `模拟面试 · 第 ${round.turn} / ${round.total} 轮` : '模拟面试'} hint={waiting ? undefined : status} plain
        action={<Button size="sm" onClick={() => setEnding(true)} disabled={!waiting}>结束并复盘</Button>}>
        <ol className="mock-turns">
          {turns.map((t, i) => (
            <li key={i}>
              <div className="mock-say" data-who="interviewer"><small>面试官</small><p>{t.question}</p></div>
              {t.answer && <div className="mock-say" data-who="me"><small>我</small><p>{t.answer}</p></div>}
            </li>
          ))}
          {asking && <li><div className="mock-say" data-who="interviewer"><small>面试官</small><p>{asking}</p></div></li>}
          {thinking && <li className="mock-thinking"><span className="spinner" />{status}</li>}
        </ol>
        <div ref={bottom} className="mock-bottom" />
      </Group>
      <form className="mock-composer float" onSubmit={(e) => { e.preventDefault(); submit() }}>
        <TextArea
          size="short"
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit() } }}
          placeholder={waiting ? '按真实面试的方式作答，输入「跳过」可跳过本题。⌘/Ctrl + Enter 发送' : '等面试官说完再回答'}
          disabled={!waiting}
          aria-label="你的回答"
        />
        <div className="mock-composer-bar">
          <span data-tone={problem ? 'error' : undefined}>{problem || (waiting ? `${answer.trim().length} 字` : '')}</span>
          <Button type="submit" size="sm" variant="primary" disabled={!waiting || !answer.trim()}>回答</Button>
        </div>
      </form>
      <ConfirmDialog
        open={ending}
        title="结束这场面试？"
        confirmLabel="结束并复盘"
        busy={false}
        error={null}
        onConfirm={() => { send({ type: 'end' }); setEnding(false) }}
        onClose={() => setEnding(false)}
      >
        只复盘已经回答过的轮次，当前这道题不算。一题都没答的话不会生成复盘。
      </ConfirmDialog>
    </section>
  )
}
