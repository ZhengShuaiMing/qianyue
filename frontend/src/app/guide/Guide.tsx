import { useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { enterSample, exitSample, SAMPLE, SNAPSHOT } from '../../demo/mode'
import {
  beginRun, introShown, markIntroShown, markSeen, needsAutoplay, OPEN_GUIDE_EVENT, takePendingRun,
  type GuideMode, type GuideRun,
} from './guide-store'
import { GuideIntro } from './GuideIntro'
import { ALL_PAGES, CLOSING, TOURS, tourFor, type GuideStep } from './tours'
import './guide.css'

/** 引导（样子参照 WaifuX 的 GuideKit）：整屏压暗，被介绍的控件挖空、描一圈强调色；换一步时挖空处滑过去，说明卡淡入。
    两种走法：本页引导只走当前页；全部引导从总览开始，按导航栏一页走完接着下一页。
    引导都用示例数据：还在你的数据里时先切过去，结束后切回你的数据。自己的数据里每一页第一次打开时自动播放本页引导；
    自己主动切到示例数据翻看时不自动播，只有主动点引导才播。
    点压暗处或「下一步」往后，Esc 或右上角「退出」结束 */

const PAD = 6 // 挖空比控件大一圈
const CARD_W = 340
const GAP = 18 // 说明卡和挖空处的距离
const EDGE = 16 // 说明卡离窗口边至少这么远
const TOP = 76 // 顶栏下沿：挖空不盖到顶栏下面（介绍顶栏里的控件时除外）

/** 正在走的引导：page 是当前这一页，index 是这一页的第几步 */
type Playing = GuideRun & { page: string; index: number }

// 为了引导刚切到示例数据、页面重新载入了：载入时取一次，接着开始（地址已经是要引导的那一页）
const PENDING = takePendingRun()

const pathOf = (pathname: string) => `/${pathname.split('/')[1]}`
const visible = (el: Element | null) => {
  const r = el?.getBoundingClientRect()
  return el && r && r.width > 0 && r.height > 0 ? el : null
}

export function Guide() {
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const [playing, setPlaying] = useState<Playing | null>(() => (PENDING ? { ...PENDING, page: PENDING.path, index: 0 } : null))
  // 第一次打开迁跃：弹入口弹窗；first 表示是第一次（标题不同）
  const [intro, setIntro] = useState<'first' | 'again' | null>(() => (!PENDING && !introShown() ? 'first' : null))
  const returnFocus = useRef<Element | null>(null)
  // 当前页面：在线演示版用 # 路由，浏览器地址的路径是 /demo/，所以一律按路由的地址算
  const here = useRef(pathOf(pathname))
  useEffect(() => { here.current = pathOf(pathname) }, [pathname])

  const play = useCallback((run: GuideRun) => {
    returnFocus.current = document.activeElement
    const page = run.mode === 'all' ? (ALL_PAGES.includes(run.path) ? run.path : ALL_PAGES[0]) : run.path
    if (here.current !== page) navigate(page)
    setPlaying({ ...run, page, index: 0 })
  }, [navigate])

  useEffect(() => { if (intro) markIntroShown() }, [intro])

  // 顶栏问号、设置页：打开入口弹窗
  useEffect(() => {
    const open = () => setIntro('again')
    window.addEventListener(OPEN_GUIDE_EVENT, open)
    return () => window.removeEventListener(OPEN_GUIDE_EVENT, open)
  }, [])

  // 每一页第一次打开时自动播放本页引导（只在自己的数据里）：还在你的数据里就先切到示例（整页重新载入），
  // 播完切回来。等页面内容出来（最多 3 秒）再播；有弹窗开着就不打扰。
  // 本地版正在看示例数据时是用户自己翻看，不自动播引导，只有主动点顶栏问号/设置里的引导才播
  const busy = playing !== null || intro !== null
  useEffect(() => {
    const page = pathOf(pathname)
    const tour = TOURS[page]
    if (busy || SAMPLE || !tour || !needsAutoplay(page) || !introShown()) return
    let tries = 0
    const timer = window.setInterval(() => {
      tries += 1
      if (document.querySelector('dialog[open]')) return
      const ready = !SNAPSHOT || tour.steps.slice(1).some((s) => s.target && visible(document.querySelector(s.target)))
      if (ready || tries > 15) {
        window.clearInterval(timer)
        const run = ready ? beginRun('page', page, enterSample) : null
        if (run) play(run)
      }
    }, 200)
    return () => window.clearInterval(timer)
  }, [pathname, busy, play])

  const choose = (mode: GuideMode | null) => {
    setIntro(null)
    const page = here.current
    markSeen(page) // 弹窗里选过了，这一页不再自动播
    if (!mode) return
    const run = beginRun(mode, mode === 'all' ? ALL_PAGES[0] : page, enterSample)
    if (run) play(run)
  }

  const end = useCallback(() => {
    if (!playing) return
    markSeen(playing.page)
    setPlaying(null)
    if (playing.exitSample) exitSample(here.current)
    else if (returnFocus.current instanceof HTMLElement) returnFocus.current.focus()
  }, [playing])

  if (intro) return <GuideIntro first={intro === 'first'} here={pathOf(pathname)} hasPage={Boolean(tourFor(pathname))} onChoose={choose} />
  if (!playing) return null

  const tour = TOURS[playing.page]
  const at = ALL_PAGES.indexOf(playing.page)
  const lastPage = playing.mode === 'page' || at === ALL_PAGES.length - 1
  const steps = playing.mode === 'all' && lastPage ? [...tour.steps, ...CLOSING] : tour.steps
  const nextPage = playing.mode === 'all' && !lastPage ? TOURS[ALL_PAGES[at + 1]] : null

  const go = (index: number) => {
    if (index < steps.length) return setPlaying({ ...playing, index: Math.max(0, index) })
    if (!nextPage) return end()
    // 这一页走完了：记下看过，到下一页从头开始
    markSeen(playing.page)
    navigate(nextPage.path)
    setPlaying({ ...playing, page: nextPage.path, index: 0 })
  }

  // 不按步重新挂载：换一步、换一页时框从上一步的位置滑到下一步
  return (
    <GuideOverlay
      stepKey={`${playing.page}:${playing.index}`}
      view={{
        step: steps[playing.index],
        index: playing.index,
        count: steps.length,
        where: playing.mode === 'all' ? `全部引导 · ${tour.name}（${at + 1}/${ALL_PAGES.length}）` : `本页引导 · ${tour.name}`,
        nextLabel: playing.index < steps.length - 1 ? '下一步' : nextPage ? `下一页：${nextPage.name}` : '完成',
      }}
      onGo={go}
      onEnd={end}
    />
  )
}

/** 一步要显示的内容 */
type View = { step: GuideStep; index: number; count: number; where: string; nextLabel: string }
/** 正在显示的那一步：控件找到、页面滚好之后才换成新的一步 */
type Shown = View & { key: string; el: Element | null; rect: DOMRect | null }

const reduceMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches

/** 控件看得见的部分太少（不到 120px，矮的控件要整个看得见）才需要滚；顶栏里的控件不用滚 */
function needsScroll(el: Element) {
  const r = el.getBoundingClientRect()
  if (r.top >= 0 && r.bottom <= TOP) return false
  const seen = Math.min(r.bottom, innerHeight) - Math.max(r.top, TOP)
  return seen < Math.min(r.height, 120)
}

/** 把控件滚进顶栏下面的可见区：放得下就居中，太高就让它的上沿露在顶栏下面 */
function scrollTo(el: Element) {
  const r = el.getBoundingClientRect()
  const room = innerHeight - TOP
  const offset = r.height > room - 32 ? r.top - TOP - 16 : r.top - TOP - (room - r.height) / 2
  window.scrollTo({ top: window.scrollY + offset, behavior: reduceMotion() ? 'auto' : 'smooth' })
}

function GuideOverlay({ stepKey, view, onGo, onEnd }: {
  stepKey: string
  view: View
  onGo: (index: number) => void
  onEnd: () => void
}) {
  const navigate = useNavigate()
  const location = useLocation()
  const [shown, setShown] = useState<Shown | null>(null)
  const [cardH, setCardH] = useState(200)
  const card = useRef<HTMLDivElement>(null)
  // 效果里要用最新的 view / onGo，但只在换步时重新跑
  const latest = useRef({ view, onGo, location })
  useLayoutEffect(() => { latest.current = { view, onGo, location } })

  // 还在找控件或滚动页面时，旧的一步留在原处，按钮先不响应
  const pending = shown?.key !== stepKey
  const next = () => { if (shown && !pending) onGo(shown.index + 1) }
  const prev = () => { if (shown && !pending) onGo(shown.index - 1) }

  // 换一步：先切到这一步要的视图，等控件出现（最多 2 秒，一直找不到就跳过这一步）；
  // 要滚就先滚，滚停了再把框和说明卡移过去
  useEffect(() => {
    const { view, onGo, location } = latest.current
    const { step } = view
    if (step.go && `${location.pathname}${location.search}` !== step.go) navigate(step.go, { replace: true })
    let timer = 0
    let frame = 0
    let tries = 0
    const commit = (el: Element | null) => setShown({ ...view, key: stepKey, el, rect: el?.getBoundingClientRect() ?? null })
    // 滚动停下来：控件位置连续几帧不变
    const settle = (el: Element) => {
      const start = performance.now()
      let last = Number.NaN
      let still = 0
      const tick = () => {
        const top = el.getBoundingClientRect().top
        still = top === last ? still + 1 : 0
        last = top
        const elapsed = performance.now() - start
        if ((still >= 3 && elapsed > 120) || elapsed > 1200) commit(el)
        else frame = requestAnimationFrame(tick)
      }
      frame = requestAnimationFrame(tick)
    }
    const find = () => {
      if (!step.target) return commit(null)
      const el = visible(document.querySelector(step.target))
      if (el && needsScroll(el)) { scrollTo(el); settle(el) }
      else if (el) commit(el)
      else if (++tries > 20) onGo(view.index + 1)
      else timer = window.setTimeout(find, 100)
    }
    timer = window.setTimeout(find, 0)
    return () => { window.clearTimeout(timer); cancelAnimationFrame(frame) }
  }, [stepKey, navigate])

  // 窗口大小变了，跟着控件重新量
  const el = shown?.el
  useEffect(() => {
    if (!el) return
    const measure = () => { if (el.isConnected) setShown((s) => (s && s.el === el ? { ...s, rect: el.getBoundingClientRect() } : s)) }
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [el])

  // 说明卡的实际高度：决定放在哪一边（在画出来之前量好，换边时直接滑过去）
  useLayoutEffect(() => {
    if (card.current) setCardH(card.current.offsetHeight)
  }, [shown?.key])

  // 播放期间页面不让手动滚；换好一步后把焦点放到主按钮上
  useEffect(() => {
    const root = document.documentElement
    const before = root.style.overflow
    root.style.overflow = 'hidden'
    return () => { root.style.overflow = before }
  }, [])
  useEffect(() => {
    if (shown) card.current?.querySelector<HTMLElement>('[data-primary]')?.focus()
  }, [shown?.key]) // eslint-disable-line react-hooks/exhaustive-deps

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') onEnd()
    else if (e.key === 'ArrowRight') next()
    else if (e.key === 'ArrowLeft' && shown && shown.index > 0) prev()
    else if (e.key === 'Tab' && card.current) {
      const items = [...card.current.querySelectorAll<HTMLElement>('button')]
      const at = items.indexOf(document.activeElement as HTMLElement)
      e.preventDefault()
      items[e.shiftKey ? (at <= 0 ? items.length - 1 : at - 1) : (at + 1) % items.length]?.focus()
    }
  }

  // 挖空只框窗口里看得见的那一截：很长的控件不会框到窗口外面去
  const rect = shown?.rect
  let hole: { top: number; left: number; width: number; height: number } | null = null
  if (rect) {
    const inBar = rect.bottom <= TOP
    const top = Math.max(rect.top, inBar ? 0 : TOP) - PAD
    const bottom = Math.min(rect.bottom, innerHeight - 8) + PAD
    const left = Math.max(rect.left, 8) - PAD
    const right = Math.min(rect.right, innerWidth - 8) + PAD
    hole = { top, left, width: right - left, height: Math.max(bottom - top, 24) }
  }

  // 说明卡放在挖空的下面、上面、右边或左边，哪边放得下放哪边；都放不下就压在挖空里靠右下
  let place: CSSProperties | undefined
  let arrow: { side: 'top' | 'bottom' | 'left' | 'right'; at: number } | null = null
  if (hole) {
    const w = Math.min(CARD_W, innerWidth - EDGE * 2)
    const cx = hole.left + hole.width / 2
    const cy = hole.top + hole.height / 2
    const clampX = (x: number) => Math.min(Math.max(x, EDGE), innerWidth - w - EDGE)
    const clampY = (y: number) => Math.min(Math.max(y, EDGE), innerHeight - cardH - EDGE)
    const bottom = hole.top + hole.height
    const right = hole.left + hole.width
    if (innerHeight - bottom >= cardH + GAP + EDGE) {
      const left = clampX(cx - w / 2)
      place = { top: bottom + GAP, left, width: w }
      arrow = { side: 'top', at: Math.min(Math.max(cx - left, 22), w - 22) }
    } else if (hole.top >= cardH + GAP + EDGE) {
      const left = clampX(cx - w / 2)
      place = { top: hole.top - GAP - cardH, left, width: w }
      arrow = { side: 'bottom', at: Math.min(Math.max(cx - left, 22), w - 22) }
    } else if (innerWidth - right >= w + GAP + EDGE) {
      const top = clampY(cy - cardH / 2)
      place = { top, left: right + GAP, width: w }
      arrow = { side: 'left', at: Math.min(Math.max(cy - top, 22), cardH - 22) }
    } else if (hole.left >= w + GAP + EDGE) {
      const top = clampY(cy - cardH / 2)
      place = { top, left: hole.left - GAP - w, width: w }
      arrow = { side: 'right', at: Math.min(Math.max(cy - top, 22), cardH - 22) }
    } else {
      place = { top: clampY(bottom - cardH - EDGE), left: clampX(right - w - EDGE), width: w }
    }
  }

  return (
    <div className="guide" data-centered={!hole && shown !== null} data-pending={pending} onKeyDown={onKeyDown}>
      <div className="guide-mask" onClick={next} aria-hidden />
      {hole ? <div className="guide-hole" style={hole} aria-hidden /> : <div className="guide-dim" aria-hidden />}
      {shown && (
        <div ref={card} className="guide-card" style={place} role="dialog" aria-modal="true" aria-labelledby="guide-title" aria-describedby="guide-text">
          {arrow && <i className="guide-arrow" data-side={arrow.side} style={arrow.side === 'top' || arrow.side === 'bottom' ? { left: arrow.at } : { top: arrow.at }} aria-hidden />}
          {/* 换一步时只有文字淡入，卡片本身从原来的位置滑过去 */}
          <div key={shown.key} className="guide-card-body">
            <div className="guide-head">
              <small>{shown.where}</small>
              <button type="button" className="guide-skip" onClick={onEnd}>退出</button>
            </div>
            <h2 id="guide-title">{shown.step.title}</h2>
            <p id="guide-text" className="guide-text">{shown.step.text}</p>
            <div className="guide-progress" aria-hidden>
              {Array.from({ length: shown.count }, (_, i) => <i key={i} data-on={i === shown.index} />)}
              <span>{shown.index + 1} / {shown.count}</span>
            </div>
            <div className="guide-actions">
              {shown.index > 0 && <button type="button" className="guide-btn" onClick={prev}>上一步</button>}
              <button type="button" className="guide-btn" data-primary onClick={next}>{shown.nextLabel}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
