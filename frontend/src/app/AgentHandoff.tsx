import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { AGENT_HANDOFF_EVENT, type Handoff, type HandoffWatch } from '../api/agent-handoff'
import { api } from '../api/client'
import { useRunAction, type Run, type RunDetail } from '../api/runs'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { IconCheck, IconCopy } from '../ui/icons'
import { useToast } from '../ui/toast'
import { runLink } from './runs/run-link'
import './agent-handoff.css'

/** 还没有配置 AI 模型时点了要用 AI 的功能：给出一句话，复制到已经连上迁跃的 Agent 里，由它替迁跃的
    模型完成；也可以去设置填 Key。复制之后（识别截图一打开就）跟着看 Agent 接手、做完 */
export function AgentHandoff() {
  // opened：第几次打开，当作 key，每次打开都从头开始，上一次的「已复制」和跟着的运行不带过来
  const [current, setCurrent] = useState<{ handoff: Handoff; opened: number } | null>(null)

  useEffect(() => {
    const open = (e: Event) => setCurrent((c) => ({ handoff: (e as CustomEvent<Handoff>).detail, opened: (c?.opened ?? 0) + 1 }))
    window.addEventListener(AGENT_HANDOFF_EVENT, open)
    return () => window.removeEventListener(AGENT_HANDOFF_EVENT, open)
  }, [])

  if (!current) return null
  return <HandoffDialog key={current.opened} handoff={current.handoff} onClose={() => setCurrent(null)} />
}

function HandoffDialog({ handoff, onClose }: { handoff: Handoff; onClose: () => void }) {
  const [copied, setCopied] = useState(false)
  // 跟着的运行结束了：那句话不用再复制，只留关闭
  const [ended, setEnded] = useState(false)
  const toast = useToast()

  const copy = () => {
    navigator.clipboard.writeText(handoff.sentence).then(
      () => {
        setCopied(true)
        handoff.onCopied?.()
        // 没有要跟着看的运行（助手）：复制完就可以关了
        if (!handoff.watch) { toast('已复制，粘贴到你的 Agent 里发出去'); onClose() }
      },
      () => toast('没有复制成功，请手动选中指令复制', 'error'),
    )
  }
  return (
    <Dialog
      open
      onClose={onClose}
      title="还没有配置 AI 模型"
      actions={
        <>
          <Link className="handoff-how" to="/settings#mcp" onClick={onClose}>如何连接 Agent（MCP）？</Link>
          <Button variant={ended ? 'primary' : undefined} onClick={onClose}>关闭</Button>
          {!ended && <Button variant="primary" onClick={copy}><IconCopy />{copied ? '再复制一次' : '复制指令'}</Button>}
        </>
      }
    >
      <p className="dialog-text">
        {handoff.what}要用 AI。请先<Link className="handoff-link" to="/settings" onClick={onClose}>在设置里配置 AI 模型</Link>；或者复制下面的指令，发送给已连接迁跃的 Agent（Codex、Claude Code、Cursor 等），由它按迁跃的规则完成，结果自动存回迁跃。
      </p>
      <p className="handoff-sentence" aria-label="发给 Agent 的指令">{handoff.sentence}</p>
      {handoff.watch && <WatchStatus watch={handoff.watch} copied={copied} onOpen={onClose} onEnded={() => setEnded(true)} />}
    </Dialog>
  )
}

/** 跟着看 Agent 做到哪了。识别截图的运行已经发起，按编号看；评估等要等 Agent 发起，
    复制之后在任务列表里找同类型、同岗位、复制之后有动静的那一次 */
function WatchStatus({ watch, copied, onOpen, onEnded }: { watch: HandoffWatch; copied: boolean; onOpen: () => void; onEnded: () => void }) {
  const byId = 'run' in watch ? watch.run : null
  const detail = useQuery({
    queryKey: ['runs', 'detail', byId],
    queryFn: () => api<RunDetail>(`/runs/${byId}`),
    enabled: byId !== null,
    refetchInterval: (query) => (query.state.data && query.state.data.status !== 'running' ? false : 2000),
  })
  const list = useQuery({
    queryKey: ['runs', 'list'],
    queryFn: () => api<{ items: Run[]; total: number }>('/runs?limit=50'),
    enabled: byId === null && copied,
    refetchInterval: 2000,
  })
  const cancel = useRunAction('cancel')
  const client = useQueryClient()

  let run: Run | undefined = detail.data
  if (!('run' in watch)) {
    const ids = [...watch.jobIds].sort().join()
    // Agent 也可能接着一次已经在进行的同样任务，所以按「复制之后有更新」认，留几秒的时钟误差
    run = list.data?.items.find((r) => r.kind === watch.kind && [...r.job_ids].sort().join() === ids
      && Date.parse(r.updated_at) >= watch.since - 5000)
  }
  // 运行一结束就刷新它存下的东西：几秒就做完的运行，任务列表的轮询可能赶不上
  const ended = run && run.status !== 'running' ? run.id : null
  useEffect(() => {
    if (!ended) return
    onEnded()
    void client.invalidateQueries({ queryKey: ['jobs'] })
    void client.invalidateQueries({ queryKey: ['runs'] })
    void client.invalidateQueries({ queryKey: ['artifacts', 'list'] })
    // onEnded 每次渲染都是新函数，只在结束的那一刻调一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ended, client])
  if (!run && !copied) return null

  const what = run?.kind !== 'screenshot' ? '打开结果' : run.job_ids.length ? '查看沟通记录' : '打开岗位'
  const link = run && <Link to={runLink(run, detail.data?.results)} onClick={onOpen}>{what}</Link>
  if (!run || (run.status === 'running' && run.agent !== 'working')) {
    return (
      <p className="handoff-status" data-tone="active" aria-live="polite">
        <span className="spinner" />等你的 Agent 接手…
        {run && byId && (
          <button type="button" className="handoff-cancel" onClick={() => cancel.mutate(run.id)} disabled={cancel.isPending}>不用了，取消</button>
        )}
      </p>
    )
  }
  const step = run.step.replace('（由你的 Agent 代答）', '')
  return (
    <p className="handoff-status" data-tone={run.status} aria-live="polite">
      {run.status === 'running' && <><span className="spinner" />你的 Agent 正在做{step && `：${step}`}</>}
      {run.status === 'waiting_user' && <><IconCheck />做好了，等你确认 {link}</>}
      {run.status === 'succeeded' && <><IconCheck />完成了 {link}</>}
      {run.status === 'failed' && <>没有完成：{run.error ?? '原因不明'}</>}
      {run.status === 'cancelled' && <>已取消</>}
    </p>
  )
}
