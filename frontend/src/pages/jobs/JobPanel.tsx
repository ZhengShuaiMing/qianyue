import { useState, type CSSProperties, type ReactNode } from 'react'
import { Link, useLocation } from 'react-router'
import { useJob, type Job } from '../../api/jobs'
import { useRunsLive } from '../../app/runs/runs-context'
import { Button } from '../../ui/Button'
import { Chip } from '../../ui/Chip'
import { EmptyState } from '../../ui/EmptyState'
import { Group } from '../../ui/Group'
import { IconChevron, IconTrash } from '../../ui/icons'
import { Menu } from '../../ui/Menu'
import { Segmented } from '../../ui/Segmented'
import { Communications } from './Communications'
import { DocFeature, type DocFeatureCopy } from './DocFeature'
import { EvaluationTab } from './Evaluation'
import { VERDICTS, fmtScore } from '../../lib/verdict'
import { sourceOf } from '../../lib/sources'
import { FEATURES, STAGES, jobName, type Feature } from './job-look'
import { JobInfoTab } from './JobInfoTab'
import { MockInterview } from './MockInterview'
import { StageDialog } from './StageDialog'
import { Tailoring } from './Tailoring'

const RESEARCH: DocFeatureCopy = {
  what: '公司研究',
  running: '正在研究这家公司',
  emptyTitle: '还没有研究这家公司',
  emptyText: '联网检索并阅读相关网页，整理成一份注明来源的研究报告，涵盖公司概况、风险与积极信号，以及面试时值得向对方提出的问题。',
  action: '开始研究',
  redo: '重新研究',
  hint: '想重点了解什么，比如是不是外包驻场（选填）',
}
const COMMUNICATION: DocFeatureCopy = {
  what: '沟通建议',
  running: '正在分析对话、起草回复',
  emptyTitle: '还没有沟通建议',
  emptyText: '没有沟通记录时，生成一段开场问候；记录招聘方的消息后，分析对方意图并起草回复。草稿需要你自行修改和发送。',
  action: '生成沟通建议',
  redo: '重新生成',
  hint: '渠道、语气、想达到的目的（选填）',
}
const INTERVIEW_PREP: DocFeatureCopy = {
  what: '面试准备',
  running: '正在准备面试',
  emptyTitle: '还没有面试准备',
  emptyText: '预测会问什么，用你的真实经历整理回答要点，并联网找这家公司的面经。',
  action: '开始准备',
  redo: '重新准备',
  hint: '第几轮、面试官是谁、哪天面、最担心什么（选填）',
}

/** 右侧详情：头部（阶段标签点了更改应聘阶段、结论、右上角的来源、公司、职位）+ 七个平级的功能页。
    换岗位时面板不重新挂载，固定的控件不动，只有文字和功能页内容淡入 */
export function JobPanel({ job, feature, onFeatureChange, onDelete }: {
  job: Job
  feature: Feature
  onFeatureChange: (feature: Feature) => void
  onDelete: () => void
}) {
  const detail = useJob(job.id)
  const [stageOpen, setStageOpen] = useState(false)
  const { search } = useLocation() // 跳到另一个岗位时留在同一个功能页
  const [editJd, setEditJd] = useState(false)
  const source = sourceOf(job.source)
  const verdict = job.evaluation?.verdict
  // 换岗位时收起正在编辑的 JD（按岗位记住上次编辑的是谁）
  const [editingFor, setEditingFor] = useState(job.id)
  if (editingFor !== job.id) { setEditingFor(job.id); setEditJd(false) }

  const waiting = useRunsLive().activeRun(job.id, 'tailoring')?.status === 'waiting_user'
  const openJdEditor = () => { setEditJd(true); onFeatureChange('info') }
  const badge = (key: Feature): ReactNode =>
    key === 'evaluation' && job.evaluation?.score != null ? <span className="feature-score">{fmtScore(job.evaluation.score)}</span>
      : key === 'info' && !job.has_jd ? <i className="feature-dot" title="还没有 JD" />
        : key === 'tailoring' && waiting ? <i className="feature-dot feature-dot-wait" title="修改方案等你确认" />
        : null

  return (
    <article className="job-panel" aria-label="岗位详情">
      <header>
        <div className="job-hero-top">
          <div className="job-hero-chips">
            <button type="button" className="chip glass chip-action" onClick={() => setStageOpen(true)} title="点这里更改应聘阶段" aria-label={`应聘阶段：${STAGES[job.stage]}，点击修改`}>
              {STAGES[job.stage]}<IconChevron />
            </button>
            {verdict && <Chip tinted>{VERDICTS[verdict]}</Chip>}
            {job.similar_to && (
              <Link className="job-dup-link" to={`/jobs/${job.similar_to}${search}`} title="公司、职位和 JD 都很像，可能是换了名字或在别的平台又发了一次。只是提示，不会合并">
                疑似与另一个岗位重复，点击查看
              </Link>
            )}
          </div>
          <div className="job-hero-side">
            <p className="job-hero-facts">
              <span className="job-hero-source" style={{ '--dot': source.color } as CSSProperties}><i />{source.label}</span>
            </p>
            <Menu label="更多操作" items={[{ label: '删除岗位', icon: <IconTrash />, danger: true, onSelect: onDelete }]} />
          </div>
        </div>
        <div className="text-in" key={job.id}>
          <h2 className="job-hero-title">{jobName(job)}</h2>
          <p className="job-hero-meta">
            {job.company && <>{job.title || '职位未填写'}<i /></>}
            {job.salary || '薪资未填写'}<i />{job.city || '城市未填写'}
          </p>
        </div>
      </header>

      <nav className="job-features" aria-label="这个岗位的功能">
        <Segmented
          fill
          items={FEATURES.map((f) => ({ key: f.key, label: <>{f.label}{badge(f.key)}</> }))}
          value={feature}
          onChange={onFeatureChange}
          label="功能"
        />
      </nav>

      <div className="job-feature-body" key={`${job.id}-${feature}`}>
        {feature === 'info' && (
          detail.data ? <JobInfoTab job={detail.data} editJd={editJd} onEditJdChange={setEditJd} />
            : detail.isError ? <EmptyState title="岗位详情没有读出来" action={<Button onClick={() => detail.refetch()}>再试一次</Button>}>{detail.error.message}</EmptyState>
              : <p className="job-loading">正在读取…</p>
        )}
        {feature === 'evaluation' && <EvaluationTab job={job} onAddJd={openJdEditor} />}
        {feature === 'communication' && (
          <>
            <Communications jobId={job.id} />
            <div className="job-later">
              <Group label="沟通建议" plain><DocFeature job={job} runKind="communication" artifactKind="communication_advice" copy={COMMUNICATION} /></Group>
            </div>
          </>
        )}
        {feature === 'research' && <DocFeature job={job} runKind="company_research" artifactKind="company_research" copy={RESEARCH} />}
        {feature === 'interview_prep' && <DocFeature job={job} runKind="interview_prep" artifactKind="interview_prep" copy={INTERVIEW_PREP} />}
        {feature === 'tailoring' && <Tailoring job={job} />}
        {feature === 'mock' && <MockInterview job={job} />}
      </div>

      {stageOpen && <StageDialog job={job} open onClose={() => setStageOpen(false)} />}
    </article>
  )
}
