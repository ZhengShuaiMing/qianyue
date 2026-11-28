import { useState, type CSSProperties } from 'react'
import { useUpdateJob, type JobDetail } from '../../api/jobs'
import { Button } from '../../ui/Button'
import { Dialog } from '../../ui/Dialog'
import { Field, TextArea, TextInput } from '../../ui/Field'
import { SOURCES } from '../../lib/sources'
import { useToast } from '../../ui/toast'

/** 编辑岗位的基本信息（含链接和来源）。只提交改过的字段 */
export function InfoDialog({ job, onClose }: { job: JobDetail; onClose: () => void }) {
  const initial = {
    company: job.company,
    title: job.title,
    salary: job.salary ?? '',
    city: job.city ?? '',
    url: job.url ?? '',
    source: job.source,
    note: job.note,
  }
  const [draft, setDraft] = useState(initial)
  const update = useUpdateJob(job.id)
  const toast = useToast()
  const set = (k: keyof typeof initial) => (e: { target: { value: string } }) => setDraft((d) => ({ ...d, [k]: e.target.value }))
  const changes = Object.fromEntries(
    (Object.keys(initial) as (keyof typeof initial)[]).filter((k) => draft[k].trim() !== initial[k]).map((k) => [k, draft[k].trim()]),
  )
  const changed = Object.keys(changes).length > 0

  const submit = () => {
    if (!changed || update.isPending) return
    update.mutate(changes, { onSuccess: () => { toast('已保存'); onClose() } })
  }

  return (
    <Dialog
      open
      onClose={onClose}
      onSubmit={submit}
      size="wide"
      title="编辑基本信息"
      actions={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button type="submit" variant="primary" disabled={!changed || update.isPending}>{update.isPending ? '正在保存…' : '保存'}</Button>
        </>
      }
    >
      <div className="field-row">
        <Field label="公司"><TextInput value={draft.company} onChange={set('company')} autoFocus /></Field>
        <Field label="职位"><TextInput value={draft.title} onChange={set('title')} /></Field>
      </div>
      <div className="field-row">
        <Field label="薪资"><TextInput value={draft.salary} onChange={set('salary')} placeholder="比如 25-35K·14薪" /></Field>
        <Field label="城市"><TextInput value={draft.city} onChange={set('city')} /></Field>
      </div>
      <Field label="链接" hint="选填，清空就是去掉链接">
        <TextInput type="url" value={draft.url} onChange={set('url')} placeholder="https://" />
      </Field>
      <div className="field">
        <span className="field-label" id="info-source-label">来源</span>
        <div className="source-options" role="radiogroup" aria-labelledby="info-source-label">
          {SOURCES.map((s) => (
            <label key={s.key} className="source-option" data-checked={draft.source === s.key} style={{ '--dot': s.color } as CSSProperties}>
              <input type="radio" name="info-source" value={s.key} checked={draft.source === s.key} onChange={set('source')} />
              <i />{s.label}
            </label>
          ))}
        </div>
      </div>
      <Field label="备注" hint="只给自己看">
        <TextArea value={draft.note} onChange={set('note')} size="short" />
      </Field>
      {update.isError && <p className="dialog-text" data-tone="error">没有保存成功：{update.error.message}</p>}
    </Dialog>
  )
}
