import { useState, type CSSProperties, type DragEvent } from 'react'
import type { Job, JobStage } from '../../api/jobs'
import { fmtScore } from '../../lib/verdict'
import { sourceOf } from '../../lib/sources'
import { Menu } from '../../ui/Menu'
import { jobName, STAGES, tileStyle, tintOf } from './job-look'
import { StageDialog } from './StageDialog'

/** 看板的列：按应聘进展从左到右。未通过和已放弃都算结束，放在最后一列 */
const COLUMNS: { key: string; label: string; stages: JobStage[] }[] = [
  { key: 'not_applied', label: '未投递', stages: ['not_applied'] },
  { key: 'applied', label: '已投递', stages: ['applied'] },
  { key: 'assessment', label: '已笔试', stages: ['assessment'] },
  { key: 'interviewed', label: '已面试', stages: ['interviewed'] },
  { key: 'offer', label: '已获 Offer', stages: ['offer'] },
  { key: 'closed', label: '已结束', stages: ['rejected', 'withdrawn'] },
]
const DRAG_TYPE = 'application/x-qianyue-job'

/** 岗位看板：每列一个应聘阶段。把卡片拖到别的列，或者用卡片上的「移到…」，都打开改阶段的弹窗，
    目标阶段已经选好，填上日期确认才改。点卡片打开这个岗位的详情 */
export function Board({ jobs, onOpen }: { jobs: Job[]; onOpen: (job: Job) => void }) {
  const [moving, setMoving] = useState<{ job: Job; to: JobStage } | null>(null)
  const [dragging, setDragging] = useState<number | null>(null)
  const [over, setOver] = useState<string | null>(null)
  const byId = new Map(jobs.map((j) => [j.id, j]))

  const move = (job: Job, to: JobStage) => { if (job.stage !== to) setMoving({ job, to }) }
  const drop = (column: (typeof COLUMNS)[number]) => (e: DragEvent) => {
    e.preventDefault()
    setOver(null)
    const job = byId.get(Number(e.dataTransfer.getData(DRAG_TYPE)))
    // 拖回自己那一列不算改
    if (job && !column.stages.includes(job.stage)) move(job, column.stages[0])
  }

  return (
    <div className="board" data-dragging={dragging !== null}>
      {COLUMNS.map((column) => {
        const cards = jobs.filter((j) => column.stages.includes(j.stage))
        return (
          <section
            key={column.key}
            className="board-col"
            data-over={over === column.key}
            aria-label={column.label}
            onDragOver={(e) => { if (e.dataTransfer.types.includes(DRAG_TYPE)) { e.preventDefault(); setOver(column.key) } }}
            onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOver((o) => (o === column.key ? null : o)) }}
            onDrop={drop(column)}
          >
            <h2 className="board-col-head">{column.label}<span>{cards.length}</span></h2>
            <ul className="board-cards">
              {cards.map((job) => (
                <BoardCard
                  key={job.id}
                  job={job}
                  dragging={dragging === job.id}
                  onDragStart={(e) => { e.dataTransfer.setData(DRAG_TYPE, String(job.id)); e.dataTransfer.effectAllowed = 'move'; setDragging(job.id) }}
                  onDragEnd={() => { setDragging(null); setOver(null) }}
                  onOpen={() => onOpen(job)}
                  moveTo={COLUMNS.filter((c) => !c.stages.includes(job.stage)).map((c) => ({ label: `移到「${c.label}」`, onSelect: () => move(job, c.stages[0]) }))}
                />
              ))}
            </ul>
            {cards.length === 0 && <p className="board-empty">{over === column.key ? `放到这里改为「${column.label}」` : '没有岗位'}</p>}
          </section>
        )
      })}
      {moving && <StageDialog key={`${moving.job.id}:${moving.to}`} job={moving.job} open suggested={moving.to} onClose={() => setMoving(null)} />}
    </div>
  )
}

// 卡片上写哪天走到这一步：「11-20 投递」，跟进时看得出放了多久。结束的那列还要分清是没通过还是放弃了
const DID: Partial<Record<JobStage, string>> = { applied: '投递', assessment: '笔试', interviewed: '面试', offer: '拿到 Offer' }
function stageNote(job: Job): string {
  const what = DID[job.stage] ?? STAGES[job.stage]
  return job.stage_on ? `${job.stage_on.slice(5)} ${what}` : STAGES[job.stage]
}

function BoardCard({ job, dragging, onDragStart, onDragEnd, onOpen, moveTo }: {
  job: Job
  dragging: boolean
  onDragStart: (e: DragEvent) => void
  onDragEnd: () => void
  onOpen: () => void
  moveTo: { label: string; onSelect: () => void }[]
}) {
  const score = job.evaluation?.score ?? null
  const sub = [job.company ? job.title : '', job.salary].filter(Boolean).join(' · ')
  return (
    <li
      className="board-card glass"
      draggable
      data-dragging={dragging}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      style={{ '--card-tint': tintOf(job) } as CSSProperties}
    >
      {/* 整张卡片都能点开；「移到…」菜单是卡片里单独的按钮，盖在它上面 */}
      <button type="button" className="board-card-open" onClick={onOpen} aria-label={`打开 ${jobName(job)}`} />
      <span className="job-tile" data-size="sm" style={tileStyle(job)}>{jobName(job).slice(0, 1)}<i title={sourceOf(job.source).label} /></span>
      <span className="board-card-main">
        <strong>{jobName(job)}</strong>
        {sub && <small>{sub}</small>}
      </span>
      <span className="board-card-foot">
        {score !== null && <span className="board-card-score">{fmtScore(score)}</span>}
        {job.stage !== 'not_applied' && <span className="board-card-stage">{stageNote(job)}</span>}
        <span className="board-card-menu"><Menu label="移到…" items={moveTo} /></span>
      </span>
    </li>
  )
}
