import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import { assistantHandoff, openAgentHandoff } from '../../api/agent-handoff'
import { useHealth } from '../../api/health'
import { streamEvents, uploadScreenshots, useConversation, useConversations, useDeleteConversation, useRenameConversation, type ChatMessage, type ConversationSummary, type PendingAction } from '../../api/chat'
import { fmtClock, fmtTime } from '../../lib/format'
import { Md } from '../../reports/Md'
import { Button } from '../../ui/Button'
import { ConfirmDialog } from '../../ui/ConfirmDialog'
import { Dialog } from '../../ui/Dialog'
import { EmptyState } from '../../ui/EmptyState'
import { Field, TextArea, TextInput } from '../../ui/Field'
import { IconImage, IconPlus, IconTrash } from '../../ui/icons'
import { ShotStrip } from '../../ui/ShotStrip'
import { useScreenshots } from '../../ui/use-screenshots'
import { Menu } from '../../ui/Menu'
import { useToast } from '../../ui/toast'
import './assistant.css'


// 空对话时的例子，点了填进输入框
const EXAMPLES = ['添加这个岗位并评估：（粘贴 JD）', 'XX 公司的岗位已经投递了', '研究一下 XX 公司', '帮我把一段经历整理成 STAR']
/** 这一轮还在进行时，流式收到的内容 */
type LiveItem =
  | { kind: 'text'; text: string }
  | { kind: 'tool'; label: string; detail: string }
  | { kind: 'run'; runId: string; name: string; progress: string }
  | { kind: 'result'; result: NonNullable<ChatMessage['result']>; text: string }
type Live = { user: string | null; images: string[]; at: string; items: LiveItem[]; confirm: PendingAction[] }

// 和 assistant.css 里两栏、一栏的分界一致。分半屏（约 960px）时还是两栏，对话列表和输入框都在眼前
const WIDE = '(min-width: 861px)'

/** 宽屏时助手页正好占满窗口剩下的高度：页面本身不滚，只有对话那一栏滚。窄屏时照常整页滚 */
function useFillWindow() {
  const ref = useRef<HTMLDivElement>(null)
  const [height, setHeight] = useState<number | undefined>()
  useLayoutEffect(() => {
    const fit = () => {
      const el = ref.current
      if (!el || !window.matchMedia(WIDE).matches) return setHeight(undefined)
      setHeight(window.innerHeight - (el.getBoundingClientRect().top + window.scrollY) - 16)
    }
    fit()
    window.addEventListener('resize', fit)
    return () => window.removeEventListener('resize', fit)
  }, [])
  return [ref, height] as const
}

/** 助手页：左边是对话列表，右边是对话。地址里记着当前对话（/assistant/编号），刷新后还在。
    别的页面可以带一句话过来（/assistant?say=…），填进新对话的输入框，由你自己点发送 */
export function AssistantPage() {
  const { conversationId } = useParams()
  const id = conversationId ?? null
  const navigate = useNavigate()
  const list = useConversations()
  const [params, setParams] = useSearchParams()
  const [say] = useState(() => params.get('say') ?? '')
  useEffect(() => { if (params.has('say')) setParams({}, { replace: true }) }, [params, setParams])
  const [fillRef, height] = useFillWindow()
  return (
    <div className="assistant" ref={fillRef} style={height ? { height } : undefined} data-fill={height !== undefined}>
      <h1 className="page-title">助手</h1>
      <div className="assistant-split">
        <aside className="assistant-list">
          <Button onClick={() => navigate('/assistant')}><IconPlus />新对话</Button>
          <ul>
            {list.data?.map((c) => (
              <ConversationItem key={c.id} conversation={c} current={c.id === id} onDeleted={() => { if (c.id === id) navigate('/assistant', { replace: true }) }} />
            ))}
          </ul>
          {list.isError && <p className="assistant-note" data-tone="error">对话列表没有读出来：{list.error.message}</p>}
        </aside>
        <Chat key={id ?? 'new'} id={id} initial={id ? '' : say} onCreated={(newId) => navigate(`/assistant/${newId}`, { replace: true })} />
      </div>
    </div>
  )
}

/** 对话列表的一项：点了打开，右边「…」里改名、删除 */
function ConversationItem({ conversation: c, current, onDeleted }: { conversation: ConversationSummary; current: boolean; onDeleted: () => void }) {
  const [renaming, setRenaming] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [title, setTitle] = useState(c.title)
  const rename = useRenameConversation()
  const remove = useDeleteConversation()
  const toast = useToast()
  const closeRename = () => { rename.reset(); setRenaming(false) }
  const closeDelete = () => { remove.reset(); setDeleting(false) }
  return (
    <li className="assistant-row">
      <Link to={`/assistant/${c.id}`} className="assistant-item" aria-current={current ? 'page' : undefined}>
        <strong>{c.title}</strong>
        <small>{fmtTime(c.updated_at)}{c.waiting && <span>等你确认</span>}</small>
      </Link>
      <div className="assistant-row-menu">
        <Menu label="这段对话的操作" items={[
          { label: '重命名', onSelect: () => { setTitle(c.title); setRenaming(true) } },
          { label: '删除', icon: <IconTrash />, danger: true, onSelect: () => setDeleting(true) },
        ]} />
      </div>
      <Dialog
        open={renaming}
        onClose={closeRename}
        onSubmit={() => title.trim() && rename.mutate({ id: c.id, title: title.trim() }, { onSuccess: () => { toast('已重命名'); closeRename() } })}
        title="重命名对话"
        actions={<><Button onClick={closeRename}>取消</Button><Button type="submit" variant="primary" disabled={!title.trim() || rename.isPending}>保存</Button></>}
      >
        <Field label="名称" hint="最多 40 个字"><TextInput value={title} onChange={(e) => setTitle(e.target.value)} maxLength={40} autoFocus /></Field>
        {rename.isError && <p className="dialog-text" data-tone="error">重命名失败：{rename.error.message}</p>}
      </Dialog>
      <ConfirmDialog
        open={deleting}
        title={`删除「${c.title}」？`}
        confirmLabel="删除"
        busy={remove.isPending}
        error={remove.isError ? remove.error.message : null}
        onConfirm={() => remove.mutate(c.id, { onSuccess: () => { toast('已删除对话'); closeDelete(); onDeleted() } })}
        onClose={closeDelete}
      >
        删除后，这段对话的全部记录无法恢复。对话中生成的评估报告、公司研究等成果仍保留在「成果」页，岗位和应聘阶段不受影响。
      </ConfirmDialog>
    </li>
  )
}

function Chat({ id, initial, onCreated }: { id: string | null; initial: string; onCreated: (id: string) => void }) {
  const history = useConversation(id)
  const [live, setLive] = useState<Live | null>(null)
  const [input, setInput] = useState(initial)
  const [busy, setBusy] = useState(false)
  const conversationRef = useRef(id)
  const bottom = useRef<HTMLDivElement>(null)
  const composer = useRef<HTMLTextAreaElement>(null)
  // 切换对话时 Chat 会按 key 卸载：这一轮的流还没读完，别把事件、报错和导航写到新对话里。
  // 不主动 abort 请求，让后端把这一轮完整跑完、历史不缺一半；只是不再往已卸载的页面上写。
  // StrictMode 挂载时会先 cleanup 再 setup，所以 setup 里要重置回来，否则开发模式下会一直是 false。
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  // 助手要用模型 Key。没有 Key 时发送就是把这句话（和截图）交给用户自己的 Agent
  const noKey = useHealth().data?.llm_configured === false
  const shots = useScreenshots()
  const client = useQueryClient()
  const toast = useToast()

  const waiting = live?.confirm.length ? live.confirm : busy ? [] : history.data?.waiting ?? []
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) }, [history.data, live])

  /** 跑一轮：发消息或确认。流里的事件逐条放进 live，结束后重新读历史再收起 live。
      返回这一轮有没有成功发出去：失败时输入框里的话放回去，截图也留着 */
  const runTurn = async (path: string, body: unknown, user: string | null, images: string[] = []): Promise<boolean> => {
    setBusy(true)
    setLive({ user, images, at: new Date().toISOString(), items: [], confirm: [] })
    const push = (change: (items: LiveItem[]) => LiveItem[]) => setLive((l) => (l ? { ...l, items: change(l.items) } : l))
    try {
      await streamEvents(path, body, (name, data) => {
        if (name === 'conversation' && !conversationRef.current) {
          conversationRef.current = data.id as string
        } else if (name === 'token') {
          push((items) => {
            const last = items[items.length - 1]
            return last?.kind === 'text' ? [...items.slice(0, -1), { kind: 'text', text: last.text + data.text }] : [...items, { kind: 'text', text: data.text as string }]
          })
        } else if (name === 'tool') {
          push((items) => [...items, { kind: 'tool', label: data.label as string, detail: data.detail as string }])
        } else if (name === 'run') {
          push((items) => [...items, { kind: 'run', runId: data.run_id as string, name: data.name as string, progress: '' }])
        } else if (name === 'progress') {
          push((items) => items.map((it) => (it.kind === 'run' && it.runId === data.run_id ? { ...it, progress: data.label as string } : it)))
        } else if (name === 'result') {
          const { text, ...result } = data as unknown as NonNullable<ChatMessage['result']> & { text: string }
          push((items) => [...items, { kind: 'result', result, text }])
        } else if (name === 'confirm') {
          setLive((l) => (l ? { ...l, confirm: data.actions as PendingAction[] } : l))
        } else if (name === 'error') {
          if (mounted.current) toast(`助手出错了：${data.message}`, 'error')
        }
      })
      const newId = conversationRef.current
      await Promise.all([
        client.invalidateQueries({ queryKey: ['chat'] }),
        client.invalidateQueries({ queryKey: ['jobs'] }),
        client.invalidateQueries({ queryKey: ['runs'] }),
        client.invalidateQueries({ queryKey: ['artifacts', 'list'] }),
      ])
      if (!id && newId) {
        if (mounted.current) onCreated(newId)
        return true
      }
      setLive(null)
      return true
    } catch (error) {
      // 已经切到别的对话时，别把旧请求的错误弹到新页面上
      if (mounted.current) {
        toast((error as Error).message, 'error')
        if (user) setInput(user)
        setLive(null)
      }
      return false
    } finally {
      setBusy(false)
    }
  }

  const send = async () => {
    const text = input.trim()
    if ((!text && !shots.shots.length) || busy || waiting.length) return
    const images = await shots.dataUrls()
    if (images === null) return
    if (noKey) return handToAgent(text, images)
    setInput('')
    const sent = await runTurn('/chat/stream', { message: text, images, conversation_id: conversationRef.current ?? undefined }, text, images)
    if (sent) shots.clear()
  }
  const handToAgent = async (text: string, images: string[]) => {
    setBusy(true)
    try {
      const upload = images.length ? await uploadScreenshots(images) : undefined
      // 复制走了才清空：用户关掉弹窗时，打的字和截图都还在
      openAgentHandoff(assistantHandoff(text, upload, () => { setInput(''); shots.clear() }))
    } catch (error) {
      toast((error as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }
  const answer = (approved: string[]) => {
    if (!conversationRef.current || busy) return
    void runTurn(`/chat/${conversationRef.current}/confirm`, { approved }, null)
  }

  const messages = history.data?.messages ?? []
  const empty = !id && !live
  return (
    <section className="chat">
      <div className="chat-scroll">
        {empty && (
          <div className="chat-welcome">
            <h2>今天帮你做些什么？</h2>
            <p>直接说出来就行，也可以粘贴截图。修改应聘阶段、保存材料之前，会先请你确认。</p>
            <div className="chat-examples">
              {EXAMPLES.map((example) => (
                <button key={example} type="button" onClick={() => { setInput(example); composer.current?.focus() }}>{example}</button>
              ))}
            </div>
          </div>
        )}
        {history.isError && <EmptyState title="这段对话没有读出来">{history.error.message}</EmptyState>}
        <ol className="chat-log">
          {messages.map((m, i) => <HistoryItem key={i} message={m} />)}
          {live && <LiveTurn live={live} />}
          {busy && !live?.items.length && <li className="chat-thinking"><span className="spinner" />助手正在思考</li>}
        </ol>
        {waiting.length > 0 && <ConfirmBox key={waiting.map((w) => w.id).join()} actions={waiting} busy={busy} onAnswer={answer} />}
        <div ref={bottom} className="chat-bottom" />
      </div>
      <form className="chat-composer float" {...shots.area} onSubmit={(e) => { e.preventDefault(); void send() }}>
        {shots.shots.length > 0 && (
          <div className="chat-shots-pending">
            <ShotStrip shots={shots.shots} onRemove={shots.remove} onMore={shots.pick} disabled={busy} />
          </div>
        )}
        <TextArea
          ref={composer}
          size="short"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send() } }}
          placeholder={waiting.length ? '先确认或取消上面的操作' : '回车发送，Shift + 回车换行；截图可以直接粘贴或拖进来'}
          disabled={busy || waiting.length > 0}
          aria-label="对助手说"
        />
        <div className="chat-composer-bar">
          {noKey
            ? <span>还没有配置 AI 模型，发送时会交给<Link to="/settings#mcp">你的 Agent</Link>来回复</span>
            : <span>助手不会替你投递或发消息</span>}
          <button type="button" className="chat-attach" onClick={shots.pick} disabled={busy || waiting.length > 0} aria-label="添加截图" title="添加截图"><IconImage /></button>
          {shots.input}
          <Button type="submit" size="sm" variant="primary" disabled={(!input.trim() && !shots.shots.length) || busy || waiting.length > 0}>{busy ? '正在回复…' : '发送'}</Button>
        </div>
      </form>
    </section>
  )
}

/** 助手想做、要你确认的操作。几件事时每条可以单独勾掉，只执行勾选的 */
function ConfirmBox({ actions, busy, onAnswer }: { actions: PendingAction[]; busy: boolean; onAnswer: (approved: string[]) => void }) {
  const [picked, setPicked] = useState(() => new Set(actions.map((a) => a.id)))
  const many = actions.length > 1
  const toggle = (id: string) => setPicked((s) => { const next = new Set(s); if (next.has(id)) next.delete(id); else next.add(id); return next })
  return (
    <div className="chat-confirm">
      <strong>助手准备执行{many ? `以下 ${actions.length} 项操作` : '以下操作'}，请确认</strong>
      {many ? (
        <div className="chat-confirm-list">
          {actions.map((a) => (
            <label key={a.id} className="chat-confirm-item">
              <input type="checkbox" checked={picked.has(a.id)} onChange={() => toggle(a.id)} disabled={busy} />
              <span>{a.description}</span>
            </label>
          ))}
        </div>
      ) : (
        <div className="chat-confirm-one prose"><Md>{actions[0].description}</Md></div>
      )}
      <div className="chat-confirm-bar">
        {many && <span>取消勾选的操作不会执行</span>}
        <Button size="sm" onClick={() => onAnswer([])} disabled={busy}>{many ? '都不执行' : '不执行'}</Button>
        <Button size="sm" variant="primary" onClick={() => onAnswer(many ? actions.filter((a) => picked.has(a.id)).map((a) => a.id) : [actions[0].id])} disabled={busy || (many && picked.size === 0)}>
          {many ? `执行勾选的 ${picked.size} 件` : '确认执行'}
        </Button>
      </div>
    </div>
  )
}

function Time({ at }: { at?: string }) {
  return at ? <time className="chat-time" dateTime={at}>{fmtClock(at)}</time> : null
}

/** 你发的截图：一排小图，点开看原图 */
function SentImages({ images }: { images: string[] }) {
  return (
    <ul className="chat-images">
      {images.map((src, i) => (
        <li key={i}><a href={src} target="_blank" rel="noreferrer" title={`看第 ${i + 1} 张原图`}><img src={src} alt={`第 ${i + 1} 张截图`} /></a></li>
      ))}
    </ul>
  )
}

function HistoryItem({ message: m }: { message: ChatMessage }) {
  if (m.role === 'user') {
    return (
      <li className="chat-say" data-who="me">
        {m.images && <SentImages images={m.images} />}
        {m.text && <p>{m.text}</p>}
        <Time at={m.at} />
      </li>
    )
  }
  if (m.role === 'summary') {
    return <li><details className="chat-summary"><summary>较早的对话已经压缩成摘要</summary><div className="prose"><Md>{m.text}</Md></div></details></li>
  }
  if (m.role === 'result' && m.result) return <li><ResultCard result={m.result} text={m.text} /></li>
  // 只调了工具、没说话的一步：只列工具，不画空气泡
  if (!m.text) return <li className="chat-tools">{m.tools.map((t, i) => <span key={i}>{t}</span>)}</li>
  return (
    <li className="chat-say" data-who="assistant">
      {m.tools.length > 0 && <div className="chat-tools">{m.tools.map((t, i) => <span key={i}>{t}</span>)}</div>}
      {m.text && <div className="prose"><Md>{m.text}</Md></div>}
      <Time at={m.at} />
    </li>
  )
}

function LiveTurn({ live }: { live: Live }) {
  return (
    <>
      {(live.user || live.images.length > 0) && (
        <li className="chat-say" data-who="me">
          {live.images.length > 0 && <SentImages images={live.images} />}
          {live.user && <p>{live.user}</p>}
          <Time at={live.at} />
        </li>
      )}
      {live.items.map((it, i) => (
        <li key={i}>
          {it.kind === 'text' && <div className="chat-say" data-who="assistant"><div className="prose"><Md>{it.text}</Md></div></div>}
          {it.kind === 'tool' && <div className="chat-tools"><span>{it.label}</span>{it.detail && <small>{it.detail}</small>}</div>}
          {it.kind === 'run' && <div className="chat-run"><span className="spinner" />{it.name}{it.progress && <small>{it.progress}</small>}</div>}
          {it.kind === 'result' && <ResultCard result={it.result} text={it.text} />}
        </li>
      ))}
    </>
  )
}

/** 专员成果开头的原文：由程序原样给出，助手不转述 */
function ResultCard({ result, text }: { result: NonNullable<ChatMessage['result']>; text: string }) {
  return (
    <article className="chat-result">
      <header><strong>{result.title}</strong><Link to={`/artifacts/${result.artifact_id}`}>打开完整成果</Link></header>
      <div className="prose"><Md>{text}</Md></div>
      {result.truncated && <p className="chat-result-more">这里只显示开头，完整内容请点上方「打开完整成果」</p>}
    </article>
  )
}
