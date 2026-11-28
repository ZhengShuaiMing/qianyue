import { useId, useState } from 'react'
import { useAddJob, type Job } from '../../api/jobs'
import { Button } from '../../ui/Button'
import { Dialog } from '../../ui/Dialog'
import { Field, TextInput } from '../../ui/Field'
import { ShotBox, ShotHint } from '../../ui/ShotBox'
import { useToast } from '../../ui/toast'
import { useScreenshots } from '../../ui/use-screenshots'
import { jobName } from './job-look'

const EMPTY = { jd_text: '', url: '', company: '', title: '', city: '', salary: '' }

/** 添加岗位：贴 JD 全文、截图或链接，至少一个。截图放在 JD 框里，下面还能接着打字；
    添加时一起交给模型读，读出的 JD 和打的字一起保存。关掉再打开时，没提交的内容还在 */
export function AddJobDialog({ open, onClose, onAdded }: { open: boolean; onClose: () => void; onAdded: (job: Job) => void }) {
  const [draft, setDraft] = useState(EMPTY)
  const [missing, setMissing] = useState(false)
  const add = useAddJob()
  const shots = useScreenshots(() => { setMissing(false); add.reset() })
  const jdId = useId()
  const toast = useToast()
  const set = (key: keyof typeof EMPTY) => (e: { target: { value: string } }) => {
    setDraft((d) => ({ ...d, [key]: e.target.value }))
    setMissing(false)
  }

  const hasShots = shots.shots.length > 0
  const submit = async () => {
    if (add.isPending) return
    if (!draft.jd_text.trim() && !draft.url.trim() && !hasShots) return setMissing(true)
    const images = await shots.dataUrls()
    if (images === null) return
    add.mutate(
      {
        images,
        jd_text: draft.jd_text.trim(),
        url: draft.url.trim() || null,
        company: draft.company.trim(),
        title: draft.title.trim(),
        city: draft.city.trim() || null,
        salary: draft.salary.trim() || null,
      },
      {
        onSuccess: (result) => {
          setDraft(EMPTY)
          shots.clear()
          add.reset()
          // 没有模型 Key：截图交给了用户的 Agent，读完才有岗位，这里先关掉
          if ('handedOff' in result) return onClose()
          const { job, created } = result
          toast(created ? `已添加「${jobName(job)}」` : `「${jobName(job)}」之前添加过，没有重复添加`)
          onAdded(job)
        },
      },
    )
  }

  const onlyUrl = !draft.jd_text.trim() && !hasShots && draft.url.trim()
  return (
    <Dialog
      open={open}
      onClose={onClose}
      onSubmit={() => void submit()}
      size="wide"
      title="添加岗位"
      actions={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button type="submit" variant="primary" disabled={add.isPending}>
            {add.isPending ? (hasShots ? '正在读截图…' : '正在添加…') : '添加'}</Button>
        </>
      }
    >
      <Field label="职位描述（JD）" htmlFor={jdId} hint={<ShotHint what="岗位信息或招聘页截图" onPick={shots.pick} />}>
        <ShotBox
          id={jdId}
          shots={shots}
          value={draft.jd_text}
          onChange={set('jd_text')}
          placeholder={hasShots ? '还可以接着写截图里没有的内容' : '岗位职责、任职要求、薪资福利、公司介绍……'}
          disabled={add.isPending}
          autoFocus
        />
      </Field>
      <Field label="链接" hint="选填">
        <TextInput type="url" value={draft.url} onChange={set('url')} placeholder="https://" />
      </Field>
      <div className="field-row">
        <Field label="公司" hint="选填">
          <TextInput value={draft.company} onChange={set('company')} />
        </Field>
        <Field label="职位" hint="选填">
          <TextInput value={draft.title} onChange={set('title')} />
        </Field>
      </div>
      <div className="field-row">
        <Field label="城市" hint="选填">
          <TextInput value={draft.city} onChange={set('city')} />
        </Field>
        <Field label="薪资" hint="选填">
          <TextInput value={draft.salary} onChange={set('salary')} />
        </Field>
      </div>
      <p className="add-note" data-tone={missing || add.isError ? 'error' : undefined} aria-live="polite">
        {missing
          ? 'JD、截图和链接至少填写一项'
          : add.isError
            ? `没有添加成功：${add.error.message}`
            : onlyUrl
              ? '只有链接时，岗位会先记为「JD 不完整」，补上 JD 后才能评估'
              : '同一个链接或同一份 JD 只会保留一条'}
      </p>
    </Dialog>
  )
}
