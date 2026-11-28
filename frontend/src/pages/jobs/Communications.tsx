import { useState } from 'react'
import { useAddCommunication, useCommunications, useDeleteCommunication, type Communication, type Speaker } from '../../api/communications'
import { fmtTime } from '../../lib/format'
import { Button } from '../../ui/Button'
import { ConfirmDialog } from '../../ui/ConfirmDialog'
import { Group } from '../../ui/Group'
import { IconTrash } from '../../ui/icons'
import { Segmented } from '../../ui/Segmented'
import { ShotBox, ShotHint } from '../../ui/ShotBox'
import { useToast } from '../../ui/toast'
import { useScreenshots } from '../../ui/use-screenshots'

const SPEAKERS: { key: Speaker; label: string; hint: string }[] = [
  { key: 'recruiter', label: '招聘方消息', hint: '粘贴招聘方发来的一条消息' },
  { key: 'me', label: '我的消息', hint: '粘贴你发出去的一条消息' },
  { key: 'transcript', label: '完整聊天记录', hint: '粘贴一整段聊天记录，包含双方的消息' },
]
const speakerName = (s: Speaker) => ({ recruiter: '招聘方', me: '我', transcript: '聊天记录' })[s]

/** 沟通记录：和招聘方聊了什么，生成沟通建议时会读它。左边看记录，右边添加 */
export function Communications({ jobId }: { jobId: number }) {
  return (
    <div className="comm-split">
      <CommunicationLog jobId={jobId} />
      <AddCommunication jobId={jobId} />
    </div>
  )
}

function CommunicationLog({ jobId }: { jobId: number }) {
  const records = useCommunications(jobId)
  const remove = useDeleteCommunication(jobId)
  const toast = useToast()
  const [deleting, setDeleting] = useState<Communication | null>(null)
  const closeDelete = () => { remove.reset(); setDeleting(null) }
  const count = records.data?.length

  return (
    <Group label="沟通记录" hint={count ? `${count} 条` : undefined} plain>
      <div className="comm-log">
        {records.isError ? (
          <p className="comm-error">沟通记录没有读出来：{records.error.message}</p>
        ) : count === 0 ? (
          <p className="comm-empty">还没有记录。沟通内容在「添加沟通」中添加。</p>
        ) : (
          <ul className="comm-list">
            {records.data?.map((r) => (
              <li key={r.id} className="comm" data-speaker={r.speaker}>
                <div className="comm-head">
                  <strong>{speakerName(r.speaker)}</strong>
                  <span>{fmtTime(r.created_at)}</span>
                  <button type="button" className="comm-delete" onClick={() => setDeleting(r)} aria-label="删除这条记录" title="删除这条记录"><IconTrash /></button>
                </div>
                <p>{r.text}</p>
              </li>
            ))}
          </ul>
        )}
      </div>
      <ConfirmDialog
        open={deleting !== null}
        title="删除这条沟通记录？"
        confirmLabel="删除"
        busy={remove.isPending}
        error={remove.isError ? remove.error.message : null}
        onConfirm={() => deleting && remove.mutate(deleting.id, { onSuccess: () => { toast('已删除'); closeDelete() } })}
        onClose={closeDelete}
      >
        删除后找不回来。以后生成沟通建议时不会再参考这条。
      </ConfirmDialog>
    </Group>
  )
}

function AddCommunication({ jobId }: { jobId: number }) {
  const add = useAddCommunication(jobId)
  const toast = useToast()
  const [speaker, setSpeaker] = useState<Speaker>('recruiter')
  const [text, setText] = useState('')
  const shots = useScreenshots(() => add.reset())
  const hasShots = shots.shots.length > 0
  const ready = text.trim() || hasShots

  // 带截图时，截图交给模型读成聊天记录，打的字按选的人接在后面
  const save = async () => {
    if (!ready || add.isPending) return
    const images = await shots.dataUrls()
    if (images === null) return
    add.mutate({ speaker, text, images }, {
      onSuccess: (result) => {
        // 没有模型 Key 时截图交给了用户的 Agent，由弹窗跟着看，这里不提示
        if (!('handedOff' in result)) toast(result.created ? '已保存' : '这条记录已存在，未重复保存')
        setText('')
        shots.clear()
      },
    })
  }

  return (
    <Group label="添加沟通" hint={<ShotHint what="聊天截图" onPick={shots.pick} />} plain>
      <div className="comm-add">
        <Segmented items={SPEAKERS.map((s) => ({ key: s.key, label: s.label }))} value={speaker} onChange={setSpeaker} label="谁说的" />
        <ShotBox
          shots={shots}
          size="short"
          value={text}
          onChange={(e) => { setText(e.target.value); add.reset() }}
          placeholder={hasShots ? (speaker === 'transcript' ? '截图里没有的聊天内容可以写在这里，会接在截图内容之后' : `截图里没有的消息可以写在这里，记为「${SPEAKERS.find((s) => s.key === speaker)!.label}」`) : SPEAKERS.find((s) => s.key === speaker)!.hint}
          disabled={add.isPending}
          aria-label="沟通内容"
        />
        <div className="comm-add-bar">
          <span data-tone={add.isError ? 'error' : undefined}>{add.isError ? `保存失败：${add.error.message}` : ''}</span>
          <Button size="sm" variant="primary" onClick={() => void save()} disabled={!ready || add.isPending}>{add.isPending ? (hasShots ? '正在读截图…' : '正在保存…') : '保存'}</Button>
        </div>
      </div>
    </Group>
  )
}
