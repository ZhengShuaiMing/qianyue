import { useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router'
import { useRunsLive } from '../../app/runs/runs-context'
import { useAllJobs, useJob, useJobOverview, useJobs, type Job, type JobCategory, type JobSort } from '../../api/jobs'
import { useStartRun } from '../../api/runs'
import { Button, ButtonLink } from '../../ui/Button'
import { EmptyState } from '../../ui/EmptyState'
import { IconCollect, IconPlus, IconProfile, IconSortRecent, IconSortScore, IconTasks } from '../../ui/icons'
import { SearchField } from '../../ui/SearchField'
import { Segmented } from '../../ui/Segmented'
import { useToast } from '../../ui/toast'
import { AddJobDialog } from './AddJobDialog'
import { Board } from './Board'
import { DeleteJobDialog } from './DeleteJobDialog'
import { JobCard, type CardRun } from './JobCard'
import { stepRatio } from './eval-steps'
import { RUNNING_TINT } from '../../lib/verdict'
import { SOURCES } from '../../lib/sources'
import { CATEGORIES, FEATURES, tintOf, type Feature } from './job-look'
import { JobPanel } from './JobPanel'
import './jobs.css'

type CategoryKey = JobCategory | 'all'
type View = 'list' | 'board'
const VIEWS: { key: View; label: string }[] = [{ key: 'list', label: '列表' }, { key: 'board', label: '看板' }]

const SORTS: Record<JobSort, { label: string; icon: ReactNode; next: JobSort }> = {
  recent: { label: '最近添加的在前', icon: <IconSortRecent />, next: 'score' },
  score: { label: '评分高的在前', icon: <IconSortScore />, next: 'recent' },
}

/** 岗位页。地址里记着选中的岗位（/jobs/12）、类别（?cat=）、排序（?sort=）和搜索词（?q=），刷新后还在原处 */
export function JobsPage() {
  const navigate = useNavigate()
  const { jobId } = useParams()
  const [params, setParams] = useSearchParams()
  const category = (params.get('cat') as JobCategory | null) ?? null
  const sort: JobSort = params.get('sort') === 'score' ? 'score' : 'recent'
  const q = params.get('q') ?? ''
  // 列表看一个岗位的详情；看板按应聘阶段看全部岗位，拖动改阶段
  const view: View = params.get('view') === 'board' ? 'board' : 'list'
  // 功能页也记在地址里（?tab=），换岗位时停在同一个功能页
  const tabParam = params.get('tab')
  const feature: Feature = FEATURES.some((f) => f.key === tabParam) ? (tabParam as Feature) : 'info'
  const [adding, setAdding] = useState(false)
  const [deleting, setDeleting] = useState<Job | null>(null)
  // 批量评估：进入挑选模式，列表和底部的条清楚、其他地方模糊；勾好岗位、写上条件就开始
  const [picked, setPicked] = useState<Set<number> | null>(null)
  // 挑岗位做什么：批量评估，或者汇总成能力画像
  const [pickFor, setPickFor] = useState<'evaluation' | 'profile'>('evaluation')
  const [conditions, setConditions] = useState('')
  const startBatch = useStartRun()
  const live = useRunsLive()
  const toast = useToast()

  const overview = useJobOverview()
  const jobs = useJobs({ category, q, sort })
  const board = useAllJobs({ q, sort })
  const list = jobs.data?.pages.flatMap((p) => p.items) ?? []
  const total = jobs.data?.pages[0]?.total ?? 0
  // 地址里的岗位在列表里就选它。不在有两种情况：被筛选条件筛掉了，就显示列表第一个，右边不显示列表里没有的岗位；
  // 没有筛选时它一定在全部岗位里，只是在还没加载的后面几页，就单独取它
  const wanted = jobId && /^\d+$/.test(jobId) ? Number(jobId) : null
  const inList = list.find((j) => j.id === wanted)
  const filtered = category !== null || q !== ''
  const single = useJob(!inList && !filtered && list.length > 0 ? wanted : null)
  const selected = inList ?? (single.data && !filtered ? single.data : null) ?? list[0] ?? null
  // 正在评估的岗位：卡片上显示走到哪一步
  const cardRun = (job: Job): CardRun | undefined => {
    const run = live.activeRun(job.id, 'evaluation')
    if (run?.status !== 'running') return undefined
    const progress = live.progress(job.id, run.id)
    return { label: progress?.label ?? '排队中', ratio: stepRatio(progress) }
  }
  const tint = selected && view === 'list' ? (cardRun(selected) ? RUNNING_TINT : tintOf(selected)) : null
  useGlow(tint)
  const pickable = (job: Job) => job.has_jd && (pickFor === 'profile' || !cardRun(job))
  const togglePick = (job: Job) => setPicked((set) => {
    const next = new Set(set)
    if (next.has(job.id)) next.delete(job.id); else next.add(job.id)
    return next
  })
  // 一列布局时列表在详情下面，进入挑选就滚到列表
  const startPicking = (purpose: 'evaluation' | 'profile' = 'evaluation') => {
    // 挑岗位在列表里勾
    if (view === 'board') updateParams({ view: 'list' })
    setPickFor(purpose)
    // 能力画像先勾上评分 4.0 以上的岗位（最多 15 个），可以再改
    setPicked(new Set(purpose === 'profile'
      ? list.filter((j) => j.has_jd && (j.evaluation?.score ?? 0) >= 4).slice(0, 15).map((j) => j.id)
      : []))
    if (window.matchMedia('(max-width: 1100px)').matches) document.querySelector('.jobs-list-wrap')?.scrollIntoView({ behavior: 'smooth' })
  }
  const exitPicking = () => { setPicked(null); setConditions(''); startBatch.reset() }
  const evaluatePicked = () => {
    if (!picked?.size || startBatch.isPending) return
    const n = picked.size
    if (pickFor === 'profile') {
      if (n < 2) return
      startBatch.mutate(
        { kind: 'profile', job_ids: [...picked] },
        { onSuccess: () => { toast(`已开始汇总 ${n} 个岗位的能力画像，完成后可在「成果」页查看`); exitPicking() } },
      )
      return
    }
    startBatch.mutate(
      { kind: 'evaluation', job_ids: [...picked], conditions: conditions.trim() },
      { onSuccess: () => { toast(`已开始评估 ${n} 个岗位，可在岗位卡片或顶栏「任务」中查看进度`); exitPicking() } },
    )
  }

  const updateParams = (change: { cat?: CategoryKey; sort?: JobSort; q?: string; tab?: Feature; view?: View }) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      if (change.cat !== undefined) { if (change.cat === 'all') next.delete('cat'); else next.set('cat', change.cat) }
      if (change.sort !== undefined) { if (change.sort === 'score') next.set('sort', 'score'); else next.delete('sort') }
      if (change.q !== undefined) { if (change.q) next.set('q', change.q); else next.delete('q') }
      if (change.tab !== undefined) { if (change.tab === 'info') next.delete('tab'); else next.set('tab', change.tab) }
      if (change.view !== undefined) { if (change.view === 'list') next.delete('view'); else next.set('view', change.view) }
      return next
    }, { replace: true, preventScrollReset: true })
  }
  const [search, setSearch] = useSearchDraft(q, (next) => updateParams({ q: next }))
  const select = (job: Job) => {
    navigate({ pathname: `/jobs/${job.id}`, search: params.toString() }, { replace: true, preventScrollReset: true })
    // 两栏时详情一直在右边，只把页面往回收一点；一列时详情在列表上面，滚到详情
    if (window.matchMedia('(max-width: 1100px)').matches) document.querySelector('.job-panel')?.scrollIntoView({ behavior: 'smooth' })
    else window.scrollTo({ top: Math.min(window.scrollY, 380), behavior: 'smooth' })
  }
  // 看板上点开一个岗位：回到列表，选中它。清掉类别筛选，免得它被筛掉
  const openFromBoard = (job: Job) => {
    const next = new URLSearchParams(params)
    next.delete('view')
    next.delete('cat')
    navigate({ pathname: `/jobs/${job.id}`, search: next.toString() })
  }
  // 新加的岗位排在全部岗位的最前面：清掉筛选和搜索，选中它
  const showAdded = (job: Job) => {
    setAdding(false)
    setSearch('')
    navigate(`/jobs/${job.id}`, { replace: true })
  }

  const counts = overview.data
  const noJobsAtAll = counts?.total === 0

  return (
    <div className="jobs" data-picking={picked !== null} style={tint ? ({ '--tint': tint } as CSSProperties) : undefined}>
      <section>
        <h1 className="page-title">我的岗位</h1>
        <div className="jobs-tools">
          <div className="jobs-search">
            <SearchField value={search} onChange={setSearch} placeholder="搜公司或职位…" label="搜索岗位" />
          </div>
          <Button size="lg" onClick={() => setAdding(true)}><IconPlus />添加岗位</Button>
          <ButtonLink size="lg" to="/collect"><IconCollect />采集岗位</ButtonLink>
          {/* 还没有岗位时也放着，告诉用户有这两件事可做；加了岗位才能点 */}
          <Button size="lg" onClick={() => startPicking('evaluation')} disabled={picked !== null || noJobsAtAll} title={noJobsAtAll ? '先添加或采集岗位，再批量评估' : undefined}><IconTasks />批量评估</Button>
          <Button size="lg" onClick={() => startPicking('profile')} disabled={picked !== null || noJobsAtAll} title={noJobsAtAll ? '先添加或采集岗位，再汇总能力画像' : '挑几个看中的岗位，汇总它们普遍要求的能力，对照你的材料'}><IconProfile />能力画像</Button>
        </div>
      </section>

      {jobs.isError ? (
        <div className="jobs-state">
          <EmptyState title="岗位列表没有读出来" action={<Button onClick={() => jobs.refetch()}>再试一次</Button>}>
            {jobs.error.message}
          </EmptyState>
        </div>
      ) : noJobsAtAll ? (
        <div className="jobs-state">
          <EmptyState
            title="还没有岗位"
            action={<div className="jobs-state-actions"><Button variant="primary" onClick={() => setAdding(true)}><IconPlus />添加岗位</Button><ButtonLink to="/collect"><IconCollect />采集岗位</ButtonLink></div>}
          >
            粘贴一段 JD 或一个链接添加岗位，或者从招聘平台批量采集。
          </EmptyState>
        </div>
      ) : (
        <>
          <div className="jobs-filter-row">
            {view === 'list' ? <Segmented
              items={CATEGORIES.map((c) => ({
                key: c.key,
                label: <>{c.label}<span className="jobs-n">{counts ? (c.key === 'all' ? counts.total : counts.categories[c.key]) : ''}</span></>,
              }))}
              value={category ?? 'all'}
              onChange={(key) => updateParams({ cat: key })}
              label="按应聘阶段筛选"
              trailing={<SortButton sort={sort} onChange={(next) => updateParams({ sort: next })} />}
            /> : null}
            <div className="jobs-filter-end">
              <p className="jobs-sources" aria-label="来源颜色">
                {SOURCES.map((s) => <span key={s.key} style={{ '--dot': s.color } as CSSProperties}><i />{s.label}</span>)}
              </p>
              <Segmented
                items={VIEWS}
                value={view}
                onChange={(key) => updateParams({ view: key })}
                label="列表或看板"
                trailing={view === 'board' ? <SortButton sort={sort} onChange={(next) => updateParams({ sort: next })} /> : undefined}
              />
            </div>
          </div>
          {view === 'board' ? (
            board.isError ? (
              <EmptyState title="岗位没有读出来" action={<Button onClick={() => board.refetch()}>再试一次</Button>}>{board.error.message}</EmptyState>
            ) : board.data && board.data.length === 0 ? (
              <EmptyState title="没有符合的岗位" action={<Button onClick={() => { setSearch(''); updateParams({ q: '' }) }}>看全部岗位</Button>}>
                没有公司或职位包含「{q}」的岗位。
              </EmptyState>
            ) : (
              <div data-stale={board.isPlaceholderData} className="jobs-board-wrap">
                <Board jobs={board.data ?? []} onOpen={openFromBoard} />
              </div>
            )
          ) : !jobs.isPending && list.length === 0 ? (
            <EmptyState
              title="没有符合的岗位"
              action={<Button onClick={() => { setSearch(''); updateParams({ cat: 'all', q: '' }) }}>看全部岗位</Button>}
            >
              {q ? `${category ? '当前筛选下，' : ''}没有公司或职位包含「${q}」的岗位。` : '没有符合筛选条件的岗位。'}
            </EmptyState>
          ) : (
            <div className="jobs-split" data-stale={jobs.isPlaceholderData}>
              <div className="jobs-list-wrap">
                <ul className="jobs-list">
                  {list.map((job) => (
                    <li key={job.id}>
                      <JobCard
                        job={job}
                        active={job.id === selected?.id}
                        run={cardRun(job)}
                        picking={picked !== null}
                        picked={picked?.has(job.id)}
                        pickable={pickable(job)}
                        onSelect={() => (picked ? togglePick(job) : select(job))}
                      />
                    </li>
                  ))}
                </ul>
                {jobs.hasNextPage && (
                  <div className="jobs-more">
                    <Button size="sm" onClick={() => jobs.fetchNextPage()} disabled={jobs.isFetchingNextPage}>
                      {jobs.isFetchingNextPage ? '正在读取…' : `再显示 ${Math.min(50, total - list.length)} 个（还有 ${total - list.length} 个）`}
                    </Button>
                  </div>
                )}
              </div>
              {selected && (
                <JobPanel job={selected} feature={feature} onFeatureChange={(tab) => updateParams({ tab })} onDelete={() => setDeleting(selected)} />
              )}
            </div>
          )}
        </>
      )}

      {picked !== null && (
        <>
          <div className="pick-veil" aria-hidden />
          <form className="batch-bar float" role="region" aria-label={pickFor === 'profile' ? '能力画像' : '批量评估'} onSubmit={(e) => { e.preventDefault(); evaluatePicked() }}>
            <span className="batch-count">{picked.size ? `已选 ${picked.size} 个` : '在左边勾选岗位'}<small>{startBatch.isError ? <em>没有开始：{startBatch.error.message}</em> : pickFor === 'profile' ? '选 2–15 个有 JD 的岗位' : '没有 JD 或正在评估的岗位不能选'}</small></span>
            {pickFor === 'profile' ? (
              <span className="batch-cond batch-note">汇总这些岗位普遍要求的能力，对照你的材料看差在哪</span>
            ) : (
              <label className="batch-cond">
                <input value={conditions} onChange={(e) => setConditions(e.target.value)} placeholder="这批岗位共同适用的条件，例如只考虑上海（选填）" aria-label="评估条件" />
              </label>
            )}
            <Button size="sm" onClick={exitPicking}>取消</Button>
            <Button size="sm" type="submit" variant="primary" disabled={(pickFor === 'profile' ? picked.size < 2 || picked.size > 15 : picked.size === 0) || startBatch.isPending}>
              {startBatch.isPending ? '正在开始…' : pickFor === 'profile' ? '生成画像' : `评估${picked.size ? ` ${picked.size} 个` : ''}`}
            </Button>
          </form>
        </>
      )}
      <AddJobDialog open={adding} onClose={() => setAdding(false)} onAdded={showAdded} />
      <DeleteJobDialog
        job={deleting}
        onClose={() => setDeleting(null)}
        onDeleted={() => navigate({ pathname: '/jobs', search: params.toString() }, { replace: true, preventScrollReset: true })}
      />
    </div>
  )
}

/** 排序只有两种，一个图标按钮来回切：图标是现在的排序，悬停看说明 */
function SortButton({ sort, onChange }: { sort: JobSort; onChange: (next: JobSort) => void }) {
  const { label, icon, next } = SORTS[sort]
  return (
    <button type="button" className="jobs-sort" onClick={() => onChange(next)}
      title={`排序：${label}。点击改为${SORTS[next].label}`} aria-label={`排序：${label}，点击改为${SORTS[next].label}`}>
      {icon}
    </button>
  )
}

/** 搜索框里的字马上显示，停手 250 毫秒后才去搜，不是每敲一个字就请求一次 */
function useSearchDraft(committed: string, commit: (value: string) => void) {
  const [draft, setDraft] = useState(committed)
  useEffect(() => {
    if (draft.trim() === committed) return
    const timer = window.setTimeout(() => commit(draft.trim()), 250)
    return () => window.clearTimeout(timer)
  })
  return [draft, setDraft] as const
}

/** 选中岗位的结论色以 18% 混进背景光，慢慢过渡；离开岗位页时退回默认 */
function useGlow(color: string | null) {
  useEffect(() => {
    const root = document.documentElement.style
    if (color) root.setProperty('--glow', color)
    else root.removeProperty('--glow')
  }, [color])
  useEffect(() => () => { document.documentElement.style.removeProperty('--glow') }, [])
}
