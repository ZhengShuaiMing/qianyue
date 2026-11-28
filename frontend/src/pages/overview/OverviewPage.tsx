import { useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { useOverview, type FunnelStep, type Overview, type Period, type TrendUnit } from '../../api/overview'
import { sourceOf } from '../../lib/sources'
import { Button, ButtonLink } from '../../ui/Button'
import { EmptyState } from '../../ui/EmptyState'
import { Segmented } from '../../ui/Segmented'
import './overview.css'

// 卡片和折线里每个概念一种颜色（定义在 overview.css）。漏斗是有先后的几步，用同一种蓝从浅到深，越往后越深
const STEPS: Record<FunnelStep, string> = {
  total: '全部岗位', has_jd: '有完整 JD', evaluated: '评估过', applied: '投递过', interviewed: '面试过', offer: '拿到 Offer',
}
// 评分分布按结论着色，从高分段到低分段：值得投偏绿，要确认风险的黄，不建议的红；分数线和评估结论一致
const BANDS = [
  { floor: 4.5, color: 'var(--ov-green)' },
  { floor: 4.0, color: 'var(--ov-green-soft)' },
  { floor: 3.5, color: 'var(--ov-yellow)' },
  { floor: 0, color: 'var(--ov-red)' },
]
const SERIES: { key: 'added' | 'applied' | 'interviewed'; name: string; color: string }[] = [
  { key: 'added', name: '新增', color: 'var(--ov-blue)' },
  { key: 'applied', name: '投递', color: 'var(--ov-orange)' },
  { key: 'interviewed', name: '面试', color: 'var(--ov-aqua)' },
]
const UNITS: { key: TrendUnit; label: string; span: string; now: string }[] = [
  { key: 'day', label: '按天', span: '近 14 天', now: '今天' },
  { key: 'week', label: '按周', span: '近 8 周（周一开始）', now: '这周' },
  { key: 'month', label: '按月', span: '近 6 个月', now: '这个月' },
]
const score = (value: number | null) => (value === null ? '—' : value.toFixed(1))
const percent = (rate: number | null) => (rate === null ? '—' : `${Math.round(rate * 100)}%`)

/** 总览：岗位、投递和评分的统计。数字由后端一次算好，这里只负责画 */
export function OverviewPage() {
  const overview = useOverview()
  return (
    <div className="overview">
      <h1 className="page-title">总览</h1>
      {overview.isError ? (
        <EmptyState title="统计没有读出来" action={<Button onClick={() => overview.refetch()}>再试一次</Button>}>{overview.error.message}</EmptyState>
      ) : !overview.data ? (
        <p className="overview-loading">正在统计…</p>
      ) : overview.data.total === 0 ? (
        <EmptyState title="还没有岗位" action={<><ButtonLink to="/collect">去采集</ButtonLink><ButtonLink to="/jobs">去岗位页</ButtonLink></>}>
          采集或添加岗位后，这里会统计岗位走到了哪一步、哪个来源的岗位更合适、每天每周的节奏和评分分布。
        </EmptyState>
      ) : (
        <Dashboard data={overview.data} />
      )}
    </div>
  )
}

function Dashboard({ data }: { data: Overview }) {
  const count = (step: FunnelStep) => data.funnel.find((f) => f.step === step)!.count
  const applied = count('applied')
  return (
    <>
      <div className="ov-tiles">
        <Tile label="岗位总数" value={data.total} color="var(--ov-blue)" icon={<path d="M4 8h16v11H4zM9 8V5h6v3" />} note={`有完整 JD ${count('has_jd')} 个`} />
        <Tile label="已评估" value={data.evaluated} color="var(--ov-violet)" icon={<path d="M5 12.5 9.5 17 19 7.5" />} note={data.evaluated ? `平均 ${score(data.average_score)} 分` : '还没有评估过'} />
        <Tile label="值得投递" value={data.worth} color="var(--ov-green)" icon={<path d="m12 4 2.4 5 5.4.6-4 3.7 1.1 5.3L12 16l-4.9 2.6 1.1-5.3-4-3.7 5.4-.6z" />} note="评估结论为值得或优先投递" />
        <Tile label="已投递" value={applied} color="var(--ov-orange)" icon={<path d="M4 12 20 5l-5 15-3-6.5z" />} note={`占全部 ${percent(data.rates.applied)}`} />
        <Tile label="已面试" value={count('interviewed')} color="var(--ov-aqua)" icon={<path d="M5 18v-1a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4v1M12 10a3 3 0 1 0 0-6 3 3 0 0 0 0 6z" />} note={applied ? `投递后面试率 ${percent(data.rates.interviewed)}` : '还没有投递'} />
        <Tile label="已获 Offer" value={count('offer')} color="var(--ov-yellow)" icon={<path d="M12 14a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM9 13l-1.5 7L12 18l4.5 2L15 13" />} note={applied ? `投递后 Offer 率 ${percent(data.rates.offer)}` : '还没有投递'} />
        <Tile label="未通过或放弃" value={data.rejected + data.withdrawn} color="var(--ov-gray)" icon={<path d="M5 6h14v3H5zM6 9v10h12V9M10 13h4" />} note={`未通过 ${data.rejected} · 已放弃 ${data.withdrawn}`} />
      </div>

      <div className="ov-grid">
        <section className="ov-card">
          <h2>应聘阶段漏斗</h2>
          <p className="ov-hint">看岗位从添加到拿到 Offer，每一步还剩多少。哪一步比上一步少得多（转化率低），就是接下来该重点推进的地方，例如评估过的多、投递过的少，说明还有合适的岗位没投</p>
          <Funnel steps={data.funnel} total={data.total} />
        </section>

        <section className="ov-card">
          <h2>评分分布{data.average_score !== null && <span className="ov-head-note">平均 {score(data.average_score)} 分</span>}</h2>
          <p className="ov-hint">每个点是一个评估过的岗位（按最近一次评估），横着看分数，越往上添加得越晚。看分数集中在哪一段：4.0 分以上的多，说明岗位方向找对了；大多低于 3.5 时，可以调整采集的关键词，或者把材料写得更完整</p>
          {data.evaluated === 0 ? <p className="ov-none">还没有评估过的岗位。在岗位页点「评估」，或者一次勾选几个批量评估。</p> : (
            <ScoreScatter points={data.points} bands={data.scores} />
          )}
        </section>
      </div>

      <Trend trend={data.trend} />

      <section className="ov-card">
        <h2>岗位来源对比</h2>
        <p className="ov-hint">比较各个来源的岗位数量和质量。平均评分高、投递率（这个来源的岗位里投递过的比例）高的来源，值得多花时间</p>
        <div className="ov-table-wrap">
          <table className="ov-table">
            <thead><tr><th>来源</th><th>岗位</th><th className="ov-share-head">占全部</th><th>评估过</th><th>平均评分</th><th>投递过</th><th>面试过</th><th>Offer</th><th>投递率</th></tr></thead>
            <tbody>
              {data.platforms.map((p) => {
                const color = sourceOf(p.source).color
                return (
                  <tr key={p.source}>
                    <th scope="row"><span className="ov-source" style={{ '--dot': color } as CSSProperties}><i />{p.name}</span></th>
                    <td>{p.jobs}</td>
                    <td className="ov-share"><span><i style={{ width: `${(p.jobs / data.total) * 100}%`, background: color }} /></span>{percent(p.jobs / data.total)}</td>
                    <td>{p.evaluated}</td><td>{score(p.average_score)}</td><td>{p.applied}</td><td>{p.interviewed}</td><td>{p.offer}</td><td>{percent(p.applied_rate)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>
    </>
  )
}

function Tile({ label, value, note, color, icon }: { label: string; value: number | string; note: string; color: string; icon: ReactNode }) {
  return (
    <section className="ov-tile" style={{ '--c': color } as CSSProperties}>
      <div className="ov-tile-head">
        <p className="ov-tile-label">{label}</p>
        <svg className="ov-tile-icon" viewBox="0 0 24 24" aria-hidden="true">{icon}</svg>
      </div>
      <p className="ov-tile-value">{value}</p>
      <p className="ov-tile-note">{note}</p>
    </section>
  )
}

function Trend({ trend }: { trend: Overview['trend'] }) {
  const [unit, setUnit] = useState<TrendUnit>('day')
  const look = UNITS.find((u) => u.key === unit)!
  const periods = trend[unit]
  return (
    <section className="ov-card">
      <div className="ov-card-head">
        <div><h2>节奏</h2><p className="ov-hint">看{look.span}每段时间新增、投递、面试了多少岗位，了解你的求职在时间上的变化趋势。投递和面试按你更改应聘阶段时填的日期统计</p></div>
        <Segmented items={UNITS.map((u) => ({ key: u.key, label: u.label }))} value={unit} onChange={setUnit} label="按什么分段" />
      </div>
      <div className="ov-legend">
        {SERIES.map((s) => (
          <span key={s.key} style={{ '--c': s.color } as CSSProperties}><i />{s.name}<strong>{periods.reduce((sum, p) => sum + p[s.key], 0)}</strong></span>
        ))}
      </div>
      <LineChart periods={periods} unit={unit} now={look.now} />
    </section>
  )
}

const periodName = (start: string, unit: TrendUnit) => {
  const [, m, d] = start.split('-').map(Number)
  return unit === 'month' ? `${m}月` : `${m}/${d}`
}

/** 刻度：0 到一个整数上限，分 4 格，每格是整数 */
function ticks(max: number) {
  const step = Math.max(1, Math.ceil(max / 4))
  return [0, 1, 2, 3, 4].map((i) => i * step)
}

/** 跟着容器变宽变窄 */
function useWidth() {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)))
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  return [ref, width] as const
}

/** 经过每个点的平滑曲线（单调三次插值）：曲线不会冲过相邻两点的高低，0 的地方不会画到 0 以下 */
function smooth(pts: [number, number][]): string {
  const n = pts.length
  if (n < 2) return n ? `M${pts[0][0]},${pts[0][1]}` : ''
  const dx = pts.slice(1).map((p, i) => p[0] - pts[i][0])
  const slope = pts.slice(1).map((p, i) => (p[1] - pts[i][1]) / dx[i])
  const m = pts.map((_, i) => {
    if (i === 0) return slope[0]
    if (i === n - 1) return slope[n - 2]
    return slope[i - 1] * slope[i] <= 0 ? 0 : (slope[i - 1] + slope[i]) / 2
  })
  for (let i = 0; i < n - 1; i++) {
    if (slope[i] === 0) { m[i] = 0; m[i + 1] = 0; continue }
    const a = m[i] / slope[i]
    const b = m[i + 1] / slope[i]
    const h = Math.hypot(a, b)
    if (h > 3) { m[i] = (3 / h) * a * slope[i]; m[i + 1] = (3 / h) * b * slope[i] }
  }
  let d = `M${pts[0][0]},${pts[0][1]}`
  for (let i = 0; i < n - 1; i++) {
    const [x0, y0] = pts[i]
    const [x1, y1] = pts[i + 1]
    const t = dx[i] / 3
    d += `C${x0 + t},${y0 + m[i] * t} ${x1 - t},${y1 - m[i + 1] * t} ${x1},${y1}`
  }
  return d
}

/** 节奏图：三条平滑曲线，每条下面一层从线色渐隐到透明的面；新增在最下层，面试在最上层。
 * 悬停或用方向键时，竖线和三个点跟着走，旁边显示那一段的三个数 */
function LineChart({ periods, unit, now }: { periods: Period[]; unit: TrendUnit; now: string }) {
  const [box, width] = useWidth()
  const [hover, setHover] = useState<number | null>(null)
  const H = 280
  const pad = { l: 36, r: 16, t: 16, b: 28 }
  const n = periods.length
  const scale = ticks(Math.max(1, ...periods.flatMap((p) => SERIES.map((s) => p[s.key]))))
  const top = scale[scale.length - 1]
  const plotW = Math.max(0, width - pad.l - pad.r)
  const x = (i: number) => pad.l + (n > 1 ? (i * plotW) / (n - 1) : plotW / 2)
  const y = (v: number) => pad.t + (1 - v / top) * (H - pad.t - pad.b)
  const curve = (key: (typeof SERIES)[number]['key']) => smooth(periods.map((p, i) => [x(i), y(p[key])]))
  // 横轴每个标签大约要 48px，放不下就隔几个标一个，最后一个总是标
  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(plotW / 48))))
  const name = (i: number) => (i === n - 1 ? now : periodName(periods[i].start, unit) + (unit === 'week' ? ' 那周' : ''))
  const pick = (clientX: number, rect: DOMRect) => {
    const i = Math.round(((clientX - rect.left - pad.l) / plotW) * (n - 1))
    setHover(Math.min(n - 1, Math.max(0, i)))
  }
  return (
    <div className="ov-line" ref={box}>
      {width > 0 && (
        <svg
          width={width}
          height={H}
          role="img"
          aria-label={`${SERIES.map((s) => s.name).join('、')}的走势，用左右方向键看每一段的数字`}
          tabIndex={0}
          onMouseMove={(e) => pick(e.clientX, e.currentTarget.getBoundingClientRect())}
          onMouseLeave={() => setHover(null)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowRight') setHover((h) => Math.min(n - 1, (h ?? -1) + 1))
            if (e.key === 'ArrowLeft') setHover((h) => Math.max(0, (h ?? n) - 1))
          }}
          onBlur={() => setHover(null)}
        >
          <defs>
            {SERIES.map((s) => (
              <linearGradient key={s.key} id={`ov-fade-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={s.color} stopOpacity={0.28} />
                <stop offset="100%" stopColor={s.color} stopOpacity={0} />
              </linearGradient>
            ))}
          </defs>
          {scale.map((t) => (
            <g key={t}>
              <line className="ov-grid-line" x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} />
              <text className="ov-axis" x={pad.l - 8} y={y(t)} dy="0.32em" textAnchor="end">{t}</text>
            </g>
          ))}
          {periods.map((p, i) => ((n - 1 - i) % every === 0 ? (
            <text key={p.start} className="ov-axis" x={x(i)} y={H - 8} textAnchor={i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}>
              {i === n - 1 ? now : periodName(p.start, unit)}
            </text>
          ) : null))}
          {SERIES.map((s) => (
            <path key={s.key} d={`${curve(s.key)}L${x(n - 1)},${y(0)}L${x(0)},${y(0)}Z`} style={{ fill: `url(#ov-fade-${s.key})` }} />
          ))}
          {SERIES.map((s) => <path key={s.key} className="ov-path" d={curve(s.key)} style={{ stroke: s.color }} />)}
          {hover === null && SERIES.map((s) => <circle key={s.key} className="ov-dot" cx={x(n - 1)} cy={y(periods[n - 1][s.key])} r={4} style={{ fill: s.color }} />)}
          {hover !== null && (
            <g>
              <line className="ov-cross" x1={x(hover)} x2={x(hover)} y1={pad.t} y2={H - pad.b} />
              {SERIES.map((s) => <circle key={s.key} className="ov-dot" cx={x(hover)} cy={y(periods[hover][s.key])} r={5} style={{ fill: s.color }} />)}
            </g>
          )}
        </svg>
      )}
      {hover !== null && (
        <div className="ov-tip" style={{ left: Math.min(Math.max(x(hover), 80), width - 80) }}>
          <strong>{name(hover)}</strong>
          {SERIES.map((s) => <span key={s.key} style={{ '--c': s.color } as CSSProperties}><i />{s.name}<b>{periods[hover][s.key]}</b></span>)}
        </div>
      )}
    </div>
  )
}

/** 漏斗：每一步一条居中的圆角带子，宽度按占全部岗位的比例，步与步之间不相连。
 * 名字在左，数量和占全部的比例在右（第一步是 100%，各行对齐） */
function Funnel({ steps, total }: { steps: Overview['funnel']; total: number }) {
  const [box, width] = useWidth()
  const BAND = 28
  const GAP = 14
  const labelW = 96
  const valueW = 84
  const plotW = Math.max(0, width - labelW - valueW)
  const center = labelW + plotW / 2
  // 太小的一步也留一点宽度，能看见
  const w = (count: number) => (total ? Math.max(count / total, 0.04) * plotW : 0)
  const y = (i: number) => i * (BAND + GAP)
  const H = steps.length * BAND + (steps.length - 1) * GAP
  return (
    <div className="ov-funnel" ref={box}>
      {width > 0 && (
        <svg width={width} height={H} role="img" aria-label={steps.map((f) => `${STEPS[f.step]} ${f.count}`).join('，')}>
          {steps.map((f, i) => (
            <g key={f.step}>
              <rect className="ov-funnel-band" x={center - w(f.count) / 2} y={y(i)} width={w(f.count)} height={BAND} rx={BAND / 2}
                style={{ fill: `var(--ov-step-${i})` }} />
              <text className="ov-funnel-name" x={0} y={y(i) + BAND / 2} dy="0.35em">{STEPS[f.step]}</text>
              <text className="ov-funnel-value" x={width} y={y(i) + BAND / 2} dy="0.35em" textAnchor="end">
                <tspan className="ov-funnel-count">{f.count}</tspan>
<tspan dx={6}>{percent(total ? f.count / total : null)}</tspan>
              </text>
            </g>
          ))}
        </svg>
      )}
    </div>
  )
}

/** 评分分布：散点图。横轴是分数，按评估结论的分数线分成几段浅色底；纵轴是岗位添加的日期（早的在下）。
 * 每个岗位一个小点，几百上千个也放得下；同一处的点重叠时颜色叠深。每段的数量写在段的上方 */
function ScoreScatter({ points, bands }: { points: Overview['points']; bands: Overview['scores'] }) {
  const [box, width] = useWidth()
  const H = 220
  const pad = { l: 10, r: 10, t: 34, b: 24 }
  const lo = Math.min(3, Math.floor(Math.min(...points.map((p) => p.score)) * 2) / 2)
  const hi = 5
  const plotW = Math.max(0, width - pad.l - pad.r)
  const x = (score: number) => pad.l + ((score - lo) / (hi - lo)) * plotW
  const days = points.map((p) => Date.parse(p.added))
  const first = Math.min(...days)
  const last = Math.max(...days)
  // 同一天、同一个分数的点稍微错开一点，不完全重合
  const seen = new Map<string, number>()
  const y = (day: number, key: string) => {
    const n = seen.get(key) ?? 0
    seen.set(key, n + 1)
    const base = last > first ? (day - first) / (last - first) : 0.5
    return H - pad.b - 6 - base * (H - pad.t - pad.b - 12) - n * 5
  }
  const band = (score: number) => BANDS.findIndex((b) => score >= b.floor)
  const zones = BANDS.map((b, i) => ({ ...b, from: Math.max(lo, b.floor), to: i === 0 ? hi : BANDS[i - 1].floor, count: bands[i]?.count ?? 0, label: bands[i]?.label ?? '' }))
    .filter((z) => z.to > z.from)
  const ticks = Array.from({ length: Math.round((hi - lo) * 2) + 1 }, (_, i) => lo + i * 0.5)
  return (
    <div className="ov-scatter" ref={box}>
      {width > 0 && (
        <svg width={width} height={H} role="img" aria-label={zones.map((z) => `${z.label} ${z.count} 个`).join('，')}>
          {zones.map((z) => (
            <g key={z.floor}>
              <rect className="ov-scatter-zone" x={x(z.from) + 1} y={pad.t - 28} width={x(z.to) - x(z.from) - 2} height={H - pad.b - pad.t + 28} rx={10} style={{ fill: z.color }} />
              <text className="ov-scatter-count" x={x(z.from) + 10} y={pad.t - 12}>{z.count}</text>
              <text className="ov-scatter-label" x={x(z.from) + 10 + String(z.count).length * 9 + 6} y={pad.t - 12}>{z.label}</text>
            </g>
          ))}
          {points.map((p, i) => (
            <circle key={i} className="ov-scatter-dot" cx={x(p.score)} cy={y(Date.parse(p.added), `${p.added}-${p.score}`)} r={4} style={{ fill: BANDS[band(p.score)].color }} />
          ))}
          {ticks.map((t) => <text key={t} className="ov-axis" x={x(t)} y={H - 6} textAnchor={t === lo ? 'start' : t === hi ? 'end' : 'middle'}>{t.toFixed(1)}</text>)}
        </svg>
      )}
    </div>
  )
}
