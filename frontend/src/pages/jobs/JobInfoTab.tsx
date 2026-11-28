import { useLayoutEffect, useRef, useState } from 'react'
import { useUpdateJob, type JobDetail } from '../../api/jobs'
import { fmtDay, fmtTime } from '../../lib/format'
import { Button } from '../../ui/Button'
import { EmptyState } from '../../ui/EmptyState'
import { TextArea } from '../../ui/Field'
import { Group } from '../../ui/Group'
import { useToast } from '../../ui/toast'
import { InfoDialog } from './InfoDialog'
import { sourceOf } from '../../lib/sources'
import { STAGE_DATES, STAGES } from './job-look'

/** 「岗位」页：JD、基本信息、阶段历史 */
export function JobInfoTab({ job, editJd, onEditJdChange }: {
  job: JobDetail
  /** JD 是否在编辑。由面板管着，评估页的「补充 JD」也能直接打开编辑 */
  editJd: boolean
  onEditJdChange: (editing: boolean) => void
}) {
  const [editInfo, setEditInfo] = useState(false)
  return (
    <>
      <JdSection job={job} editing={editJd} onEditingChange={onEditJdChange} />
      <Group label="基本信息" action={<Button size="sm" onClick={() => setEditInfo(true)}>编辑</Button>}>
        <Row k="公司">{job.company || <Missing />}</Row>
        <Row k="职位">{job.title || <Missing />}</Row>
        <Row k="薪资">{job.salary || <Missing />}</Row>
        <Row k="城市">{job.city || <Missing />}</Row>
        {/* 采集时从平台列表读到的，没有就不占行 */}
        {job.industry && <Row k="行业">{job.industry}</Row>}
        {job.company_size && <Row k="公司规模">{job.company_size}</Row>}
        {job.posted_at && <Row k={job.source === 'liepin' ? '刷新日期' : '发布日期'}>{job.posted_at}</Row>}
        <Row k="链接">{job.url ? <a className="info-link" href={job.url} target="_blank" rel="noreferrer">{job.url}</a> : <Missing />}</Row>
        <Row k="来源">{sourceOf(job.source).label} · {fmtTime(job.created_at)}</Row>
        <Row k="备注">{job.note ? <span className="info-note">{job.note}</span> : <Missing text="没有" />}</Row>
      </Group>
      {/* 每一条是一次操作：左边是操作的时间，右边写改成了什么，以及你填的这一步实际发生的日期 */}
      <Group label="阶段历史" hint={job.events.length > 1 ? `${job.events.length} 条，最新的在上面` : undefined}>
        <div className="events">
        {[...job.events].reverse().map((e, i) => (
          <div key={i} className="event">
            <span className="event-time">{fmtTime(e.created_at)}</span>
            <span className="event-main">
              {e.from_stage
                ? <span className="event-change">从「{STAGES[e.from_stage]}」改为 <strong>{STAGES[e.to_stage]}</strong>，{STAGE_DATES[e.to_stage]}：{fmtDay(e.happened_on)}</span>
                : <strong>添加岗位</strong>}
              {e.from_stage && e.note && <span className="event-note">{e.note}</span>}
            </span>
          </div>
        ))}
        </div>
      </Group>
      {editInfo && <InfoDialog job={job} onClose={() => setEditInfo(false)} />}
    </>
  )
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return <div className="info-row"><span>{k}</span><div>{children}</div></div>
}

const Missing = ({ text = '未填写' }: { text?: string }) => <span className="info-missing">{text}</span>

/** JD：默认收起到十几行，可以展开全文；编辑在原位进行 */
function JdSection({ job, editing, onEditingChange }: { job: JobDetail; editing: boolean; onEditingChange: (editing: boolean) => void }) {
  const [draft, setDraft] = useState(job.jd_text)
  const [expanded, setExpanded] = useState(false)
  const [long, setLong] = useState(false)
  const textRef = useRef<HTMLDivElement>(null)
  const update = useUpdateJob(job.id)
  const toast = useToast()

  // 收起时内容是不是被截断了，被截断才显示「展开全文」
  useLayoutEffect(() => {
    const el = textRef.current
    if (el) setLong(el.scrollHeight > el.clientHeight + 4)
  }, [job.jd_text, editing])

  const start = () => { setDraft(job.jd_text); update.reset(); onEditingChange(true) }
  const cancel = () => { update.reset(); onEditingChange(false) }
  const empty = !draft.trim()
  const save = () => {
    if (empty || update.isPending) return
    const wasIncomplete = job.prep === 'incomplete'
    update.mutate({ jd_text: draft }, {
      onSuccess: () => {
        toast(wasIncomplete ? 'JD 已保存，岗位变成了「待评估」' : 'JD 已保存')
        onEditingChange(false)
      },
    })
  }

  if (editing) {
    return (
      <Group label="职位描述（JD）" hint={job.evaluation ? '改了 JD 不会动已有的评估报告；要按新 JD 评估，去「评估」页重新评估' : undefined}>
        <div className="jd-edit">
          <TextArea value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="岗位职责、任职要求、薪资福利……" autoFocus />
          <div className="jd-edit-bar">
            <span data-tone={update.isError || (empty && job.has_jd) ? 'error' : undefined}>
              {update.isError ? `没有保存成功：${update.error.message}` : empty ? (job.has_jd ? 'JD 不能清空' : '粘贴 JD 后才能保存') : `${draft.trim().length} 字`}
            </span>
            <Button size="sm" onClick={cancel}>取消</Button>
            <Button size="sm" variant="primary" onClick={save} disabled={empty || update.isPending}>{update.isPending ? '正在保存…' : '保存'}</Button>
          </div>
        </div>
      </Group>
    )
  }

  if (!job.has_jd) {
    return (
      <section className="group">
        <EmptyState title="还没有 JD" action={<Button variant="primary" onClick={start}>补充 JD</Button>}>
          {job.url ? '只记下了链接。打开链接把职位描述复制过来，补上之后岗位会变成「待评估」，就能评估了。' : '补上职位描述之后岗位会变成「待评估」，就能评估了。'}
        </EmptyState>
      </section>
    )
  }

  return (
    <Group label="职位描述（JD）" action={<Button size="sm" onClick={start}>编辑</Button>}>
      <div className="jd-view">
        <div ref={textRef} className="jd-text" data-expanded={expanded} data-clamped={long && !expanded}>{job.jd_text}</div>
        {(long || expanded) && (
          <button type="button" className="jd-toggle" onClick={() => setExpanded((v) => !v)}>{expanded ? '收起' : '展开全文'}</button>
        )}
      </div>
    </Group>
  )
}
