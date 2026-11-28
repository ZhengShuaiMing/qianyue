import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { Link } from 'react-router'
import { useBrowserStatus, useOpenBrowser, usePlatforms } from '../../api/collection'
import { useRun, useRunAction, useStartRun, type Run } from '../../api/runs'
import { useRunsLive } from '../../app/runs/runs-context'
import { fmtTime } from '../../lib/format'
import { sourceOf } from '../../lib/sources'
import { Button } from '../../ui/Button'
import { ProgressBar } from '../../ui/ProgressBar'
import { EmptyState } from '../../ui/EmptyState'
import { Group } from '../../ui/Group'
import { useToast } from '../../ui/toast'
import { IconChevron, IconRedo } from '../../ui/icons'
import './collect.css'

// 和后端 backend/app/collectors/scope.py 的上限一致，提前提示，后端仍会校验
const MAX_SEARCHES = 25
const MAX_TOTAL = 100

// 关键词里可以有空格（「Python 后端」是一个搜索），只按逗号、顿号分；城市和排除词也可以用空格分
const split = (text: string, by: RegExp) => [...new Set(text.split(by).map((w) => w.trim()).filter(Boolean))]
const keywords = (text: string) => split(text, /[,，、]+/)
const words = (text: string) => split(text, /[,，、\s]+/)

/** 采集页：左边填采集范围，右边是采集记录（正在采集的那次在最上面，展开显示进度） */
export function CollectPage() {
  const live = useRunsLive()
  const runs = live.runs.filter((r) => r.kind === 'collection')
  const running = runs.find((r) => r.status === 'running')
  const [open, setOpen] = useState<string | null>(null)
  // 新发起的采集自动展开；采完以后保持展开，直到你点开别的
  const [started, setStarted] = useState<string | null>(null)
  if (running && running.id !== started) {
    setStarted(running.id)
    setOpen(running.id)
  }
  return (
    <div className="collect">
      <h1 className="page-title">采集岗位</h1>
      <p className="page-intro">
        <span>用专用的 Chrome 打开招聘平台的搜索结果，按你的条件读取岗位，去重后存进「岗位」。</span>
        <span>遇到登录或验证就停下，由你在专用浏览器里自己处理。不会替你投递或发消息。</span>
      </p>
      <ChromeStatus />
      <div className="collect-split">
        <CollectForm busy={!!running} />
        <section className="collect-side">
          {runs.length === 0 ? (
            <Group label="采集记录" plain><EmptyState title="还没有采集过">选好平台、填好采集范围，点「开始采集」。</EmptyState></Group>
          ) : (
            <Group label="采集记录" hint={`最近 ${runs.length} 次，点一条展开`}>
              {runs.map((r) => (
                <RunRecord key={r.id} run={r} open={r.id === open} onToggle={() => setOpen(r.id === open ? null : r.id)} />
              ))}
            </Group>
          )}
        </section>
      </div>
    </div>
  )
}

/** 开始前先看一眼本机有没有 Chrome：采集靠它，缺了在这里就说清楚，不用等到点「开始采集」才报错 */
function ChromeStatus() {
  const status = useBrowserStatus()
  // 检测只要几十毫秒：图标至少转 0.6 秒，结束后显示「已检测」1.5 秒，让人看得出点了有反应
  const [phase, setPhase] = useState<'idle' | 'checking' | 'done'>('idle')
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const recheck = async () => {
    window.clearTimeout(timer.current)
    setPhase('checking')
    await Promise.all([status.refetch(), new Promise((resolve) => window.setTimeout(resolve, 600))])
    setPhase('done')
    timer.current = window.setTimeout(() => setPhase('idle'), 1500)
  }
  // 第一次检查很快，读取中不占位，免得一闪一闪
  if (status.isPending) return null
  const data = status.data
  const missing = status.isError || !data?.available
  return (
    <p className="collect-chrome" data-state={missing ? 'missing' : 'ready'}>
      <i aria-hidden />
      {missing ? (
        <span>
          没有找到 Chrome，采集需要本机安装 Chrome。
          <a href="https://www.google.com/chrome/" target="_blank" rel="noreferrer">下载 Chrome</a>
          不装也可以，直接在岗位页粘贴 JD 或截图。
        </span>
      ) : (
        <span>
          采集会使用本机的 Chrome
          <code title={data.path ?? undefined}>{data.path}</code>
        </span>
      )}
      <button type="button" className="collect-chrome-retry" data-phase={phase} onClick={recheck} disabled={phase === 'checking'}>
        <IconRedo />
        {phase === 'checking' ? '检测中…' : phase === 'done' ? '已检测' : '重新检测'}
      </button>
    </p>
  )
}

function CollectForm({ busy }: { busy: boolean }) {
  const platforms = usePlatforms()
  const [picked, setPicked] = useState<Record<string, number>>({})
  const [queries, setQueries] = useState('')
  const [cities, setCities] = useState('')
  const [minSalary, setMinSalary] = useState('')
  const [years, setYears] = useState('')
  const [exclude, setExclude] = useState('实习')
  const [evaluate, setEvaluate] = useState(false)
  const start = useStartRun()
  const browser = useOpenBrowser()
  const toast = useToast()

  const q = keywords(queries)
  const c = words(cities)
  const keys = Object.keys(picked)
  const searches = keys.length * q.length * c.length
  const total = keys.reduce((n, k) => n + picked[k] * q.length * c.length, 0)
  const tooBig = searches > MAX_SEARCHES || total > MAX_TOTAL
  const ready = keys.length > 0 && q.length > 0 && c.length > 0 && !tooBig

  const toggle = (key: string) => setPicked((p) => {
    const next = { ...p }
    if (key in next) delete next[key]; else next[key] = 10
    return next
  })
  const submit = () => {
    if (!ready || start.isPending) return
    start.mutate({
      kind: 'collection',
      job_ids: [],
      collection: {
        platforms: keys,
        queries: q,
        cities: c,
        per_search: 10,
        per_platform: picked,
        min_salary: minSalary ? Number(minSalary) : null,
        years: years ? Number(years) : null,
        exclude: words(exclude),
        relevant_only: true,
        evaluate,
        list_only: false,
      },
    }, { onSuccess: () => toast('开始采集，专用浏览器会自己打开'), onError: (e) => toast(`没有开始采集：${e.message}`, 'error') })
  }

  return (
    <form className="collect-form" onSubmit={(e) => { e.preventDefault(); submit() }}>
      <Group label="平台和数量" hint="勾选要采集的平台，并填写每个「关键词 + 城市」组合最多新增的岗位数（1–20）">
        {/* 要登录才读得到完整 JD 的平台排在后面，挨在一起 */}
        {platforms.data?.toSorted((a, b) => Number(a.reads_detail_pages) - Number(b.reads_detail_pages)).map((p) => {
          const on = p.key in picked
          return (
            <div key={p.key} className="collect-platform" data-on={on} style={{ '--dot': sourceOf(p.key).color } as CSSProperties}>
              <label>
                <input type="checkbox" checked={on} onChange={() => toggle(p.key)} />
                <i /><span className="collect-platform-name">{p.name}</span>
                {p.reads_detail_pages && <small className="clip" title="要先登录才能读到完整 JD">要先登录才能读到完整 JD</small>}
              </label>
              {on && (
                <span className="collect-count-wrap">
                  <input className="collect-count" type="number" min={1} max={20} value={picked[p.key]} aria-label={`${p.name}新增多少个岗位`}
                    onChange={(e) => setPicked((all) => ({ ...all, [p.key]: Math.max(1, Math.min(20, Number(e.target.value) || 1)) }))} />个
                </span>
              )}
              <button type="button" className="collect-login" onClick={() => browser.mutate(p.key, { onSuccess: (r) => toast(r.message), onError: (e) => toast(e.message, 'error') })}>
                打开登录页
              </button>
            </div>
          )
        })}
        {platforms.isError && <p className="collect-note" data-tone="error">平台列表没有读出来：{platforms.error.message}</p>}
      </Group>

      <Group label="采集范围" hint="多个用逗号隔开" plain>
        <div className="collect-fields">
          <label className="collect-field"><span>关键词</span><input value={queries} onChange={(e) => setQueries(e.target.value)} placeholder="前端开发, React 开发" /></label>
          <label className="collect-field"><span>城市</span><input value={cities} onChange={(e) => setCities(e.target.value)} placeholder="北京, 上海" /></label>
          <label className="collect-field"><span>最低月薪（千元）<small className="clip" title="选填">选填</small></span><input type="number" min={1} max={500} value={minSalary} onChange={(e) => setMinSalary(e.target.value)} placeholder="比如 25" /></label>
          <label className="collect-field"><span>我的工作年限<small className="clip" title="选填，岗位要求年限比你多一年以上跳过">选填，岗位要求年限比你多一年以上跳过</small></span><input type="number" min={0} max={50} value={years} onChange={(e) => setYears(e.target.value)} placeholder="比如 3" /></label>
          <label className="collect-field collect-field-wide"><span>排除词<small className="clip" title="选填，职位名称包含这些词时跳过">选填，职位名称包含这些词时跳过</small></span><input value={exclude} onChange={(e) => setExclude(e.target.value)} placeholder="实习, 外包" /></label>
        </div>
        <label className="collect-switch"><input type="checkbox" checked={evaluate} onChange={(e) => setEvaluate(e.target.checked)} />采集完成后自动评估新增的岗位</label>
      </Group>

      <div className="collect-submit">
        <span data-tone={tooBig ? 'error' : undefined}>
          {searches === 0 ? '选平台、填关键词和城市' : q.length * c.length === 1 ? `最多新增 ${total} 个岗位` : `${q.length} 个关键词 × ${c.length} 个城市，每个组合都按上面的数量采，最多新增 ${total} 个岗位`}
          {tooBig && `，超出上限（一次最多 ${MAX_SEARCHES} 个搜索、${MAX_TOTAL} 个岗位）`}
        </span>
        <Button type="submit" variant="primary" disabled={!ready || busy || start.isPending}>
          {busy ? '已有采集正在进行' : start.isPending ? '正在开始…' : '开始采集'}
        </Button>
      </div>
    </form>
  )
}

const STATUS = { running: '进行中', succeeded: '完成', failed: '未完成', cancelled: '已取消', waiting_user: '等你处理' } as const

/** 采集记录里的一条：收起时一行（时间、搜了什么、新增几个、状态），展开后是进度或每个搜索的结论 */
function RunRecord({ run, open, onToggle }: { run: Run; open: boolean; onToggle: () => void }) {
  const live = useRunsLive()
  const detail = useRun(run.id)
  const cancel = useRunAction('cancel')
  const resume = useRunAction('resume')
  const browser = useOpenBrowser()
  const toast = useToast()
  const lines = live.lines(run.id)
  const results = detail.data?.results ?? []
  const saved = results.flatMap((r) => r.saved ?? [])
  // 搜索是按顺序一个一个做的，第 i 个结论对应第 i 个搜索；被登录或验证挡住的平台给一个打开登录的按钮
  const searches = detail.data?.collection?.searches ?? []
  const blockedPlatforms = [...new Set(results.map((r, i) => (r.status === 'blocked' ? searches[i]?.platform : undefined)).filter((p): p is string => !!p))]
  const first = results[0]?.search ?? (searches[0] ? `${sourceOf(searches[0].platform).label}「${searches[0].query}」${searches[0].city}` : '')
  const what = searches.length > 1 ? `${first} 等 ${searches.length} 个搜索` : first
  const isRunning = run.status === 'running'

  return (
    <div className="collect-record" data-open={open}>
      <button type="button" className="collect-record-head" aria-expanded={open} onClick={onToggle}>
        <span className="collect-record-time">{fmtTime(run.created_at)}</span>
        <span className="collect-record-what">{what}</span>
        <span className="collect-record-meta">
          {saved.length > 0 && <small>新增 {saved.length}</small>}
          <small data-status={run.status}>{STATUS[run.status]}</small>
          <IconChevron />
        </span>
      </button>
      {open && (
        <div className="collect-record-body">
          {isRunning && run.total > 1 && run.done !== null && (
            <div className="collect-progress"><ProgressBar done={run.done} total={run.total} label="采完的搜索" /></div>
          )}
          {isRunning && (
            <ol className="collect-lines" aria-live="polite">
              {lines.length === 0 && <li>{run.step || '正在打开专用浏览器'}</li>}
              {lines.slice(-12).map((l, i) => <li key={i}>{l}</li>)}
            </ol>
          )}
          {results.map((r, i) => (
            <div key={i} className="collect-result" data-status={r.status}>
              <strong>{r.search}</strong>
              <p>{r.reason}</p>
            </div>
          ))}
          <div className="collect-result-foot">
            {isRunning
              ? <Button size="sm" onClick={() => cancel.mutate(run.id, { onError: (e) => toast(e.message, 'error') })} disabled={cancel.isPending}>取消</Button>
              : <span>{saved.length ? `一共新增 ${saved.length} 个岗位` : run.status === 'cancelled' ? '已取消' : '这次没有新增岗位'}</span>}
            {saved.length > 0 && <Link className="collect-link" to={`/jobs/${saved[0]}`}>在岗位列表中查看</Link>}
            {(run.status === 'failed' || run.status === 'cancelled') && (
              <Button size="sm" onClick={() => resume.mutate(run.id, { onError: (e) => toast(e.message, 'error') })} disabled={resume.isPending} title="已完成的搜索不会重复采集">从断点继续</Button>
            )}
            {blockedPlatforms.map((p) => (
              <Button key={p} size="sm" onClick={() => browser.mutate(p, { onSuccess: (r) => toast(r.message), onError: (e) => toast(e.message, 'error') })}>打开{sourceOf(p).label}登录页</Button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
