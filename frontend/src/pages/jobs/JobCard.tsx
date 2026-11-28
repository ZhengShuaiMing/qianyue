import type { CSSProperties } from 'react'
import type { Job } from '../../api/jobs'
import { IconCheck } from '../../ui/icons'
import { fmtScore } from '../../lib/verdict'
import { sourceOf } from '../../lib/sources'
import { cardLabel, jobName, tileStyle, tintOf } from './job-look'

/** 正在评估时卡片上显示的进度 */
export type CardRun = { label: string; ratio: number }

export function JobCard({ job, active, run, picking, picked, pickable, onSelect }: {
  job: Job
  active: boolean
  run?: CardRun
  /** 批量评估选岗位时，卡片左边出现勾选框，点卡片就是勾选 */
  picking?: boolean
  picked?: boolean
  pickable?: boolean
  onSelect: () => void
}) {
  const score = job.evaluation?.score ?? null
  // 列表名用了公司名时，下面一行写职位；用了职位名时就不再重复
  const meta = [job.company ? job.title : '', job.salary, job.city].filter(Boolean).join(' · ')
  const unfilled = [!job.title && '职位', '薪资', '城市'].filter(Boolean).join('、')
  return (
    <button
      type="button"
      className="job-card glass"
      aria-current={!picking && active}
      aria-pressed={picking ? picked : undefined}
      disabled={picking && !pickable}
      data-picking={picking}
      onClick={onSelect}
      style={{ '--card-tint': tintOf(job) } as CSSProperties}
    >
      <span className="job-tile" style={tileStyle(job)}>
        {jobName(job).slice(0, 1)}<i title={sourceOf(job.source).label} />
        {picking && <span className="job-check" data-on={picked}><IconCheck /></span>}
      </span>
      <span className="job-card-main">
        <span className="job-card-title">{jobName(job)}</span>
        <span className="job-card-meta">
          {job.similar_to && <span className="job-card-dup" title="公司、职位和 JD 都很像，可能是同一个岗位。只是提示，不会合并">疑似重复岗位</span>}
          {meta || `${unfilled}未填写`}
        </span>
      </span>
      <span className="job-card-side">
        {run ? (
          <span className="job-card-run"><span className="spinner" />评估中</span>
        ) : (
          <span className="job-card-score" data-empty={score === null}>{fmtScore(score)}</span>
        )}
        <span className="job-card-stage">{run ? run.label : cardLabel(job)}</span>
      </span>
      {run && <span className="job-card-progress" style={{ '--p': run.ratio } as CSSProperties} />}
    </button>
  )
}
