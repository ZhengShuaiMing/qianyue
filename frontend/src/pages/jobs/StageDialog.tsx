import { useId, useState } from 'react'
import { useChangeStage, type Job, type JobStage } from '../../api/jobs'
import { today } from '../../lib/format'
import { Button } from '../../ui/Button'
import { Dialog } from '../../ui/Dialog'
import { DatePicker } from '../../ui/DatePicker'
import { Field, TextInput } from '../../ui/Field'
import { useToast } from '../../ui/toast'
import { STAGE_DATES, STAGES } from './job-look'

// 应聘进展，按类别分组。「待评估」「已评估」是处理进度，按有没有评估报告算，不在这里改
const GROUPS: { label: string; stages: JobStage[] }[] = [
  { label: '未开始', stages: ['not_applied'] },
  { label: '进行中', stages: ['applied', 'assessment', 'interviewed'] },
  { label: '已结束', stages: ['offer', 'rejected', 'withdrawn'] },
]

/** suggested：打开时预先选好的阶段（例如沟通建议里建议的阶段），仍要你点确认才改。
    当前阶段用浅色底标出来；选中的新阶段用实色 */
export function StageDialog({ job, open, onClose, suggested }: { job: Job; open: boolean; onClose: () => void; suggested?: JobStage }) {
  const [stage, setStage] = useState<JobStage | null>(suggested ?? null)
  const [note, setNote] = useState('')
  const [day, setDay] = useState(today())
  const dayId = useId()
  const change = useChangeStage(job.id)
  const toast = useToast()
  const same = stage === null || stage === job.stage

  const close = () => { change.reset(); onClose() }
  const submit = () => {
    if (same || change.isPending) return
    change.mutate(
      { stage, note: note.trim(), happened_on: day },
      { onSuccess: () => { toast(`已改为「${STAGES[stage]}」`); close() } },
    )
  }

  return (
    <Dialog
      open={open}
      onClose={close}
      onSubmit={submit}
      title="更改应聘阶段"
      focusSelf
      actions={
        <>
          <Button onClick={close}>取消</Button>
          <Button type="submit" variant="primary" disabled={same || change.isPending}>
            {change.isPending ? '正在保存…' : same ? '选一个新阶段' : `改为「${STAGES[stage]}」`}
          </Button>
        </>
      }
    >
      {GROUPS.map((group) => (
        <fieldset key={group.label} className="stage-group">
          <legend>{group.label}</legend>
          {group.stages.map((s) => (
            <label key={s} className="stage-option" data-checked={stage === s} data-current={s === job.stage}>
              <input type="radio" name="stage" value={s} checked={stage === s} onChange={() => setStage(s)} />
              {STAGES[s]}{s === job.stage && <small>当前</small>}
            </label>
          ))}
        </fieldset>
      ))}
      <Field label={stage && !same ? STAGE_DATES[stage] : '日期'} htmlFor={dayId} hint="这一步实际发生的日期，默认今天">
        <DatePicker id={dayId} value={day} onChange={setDay} max={today()} />
      </Field>
      <Field label="备注" hint="选填，会记在阶段历史里">
        <TextInput value={note} onChange={(e) => setNote(e.target.value)} placeholder="比如：内推、一面约在周三下午" />
      </Field>
      {change.isError && <p className="dialog-text" data-tone="error">没有改成：{change.error.message}</p>}
    </Dialog>
  )
}
