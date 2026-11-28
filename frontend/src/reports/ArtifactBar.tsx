import { useState } from 'react'
import { ARTIFACT_KINDS, exportUrl, useDeleteArtifact, type ArtifactSummary } from '../api/artifacts'
import { fmtTime } from '../lib/format'
import { ConfirmDialog } from '../ui/ConfirmDialog'
import { IconButton, IconDownload } from '../ui/IconButton'
import { IconExport, IconRedo, IconTrash } from '../ui/icons'
import { useToast } from '../ui/toast'
import './report.css'

/** 成果上方的工具条：左边是生成时间，右边是重新生成（可选）、导出 Markdown、删除 */
export function ArtifactBar({ artifact, redo, onDeleted }: {
  artifact: ArtifactSummary
  redo?: { label: string; onClick: () => void }
  onDeleted?: () => void
}) {
  const [confirming, setConfirming] = useState(false)
  const remove = useDeleteArtifact()
  const toast = useToast()
  const name = ARTIFACT_KINDS[artifact.kind]
  const close = () => { remove.reset(); setConfirming(false) }
  return (
    <div className="artifact-bar">
      <span>生成于 {fmtTime(artifact.updated_at)}</span>
      {redo && <IconButton size="sm" label={redo.label} onClick={redo.onClick}><IconRedo /></IconButton>}
      <IconDownload size="sm" label="导出 Markdown" href={exportUrl(artifact.id)}><IconExport /></IconDownload>
      <IconButton size="sm" label={`删除这份${name}`} onClick={() => setConfirming(true)}><IconTrash /></IconButton>
      <ConfirmDialog
        open={confirming}
        title={`删除这份${name}？`}
        confirmLabel="删除"
        busy={remove.isPending}
        error={remove.isError ? remove.error.message : null}
        onConfirm={() => remove.mutate(artifact.id, { onSuccess: () => { toast(`已删除${name}`); close(); onDeleted?.() } })}
        onClose={close}
      >
        {artifact.kind === 'evaluation'
          ? '删除后找不回来。岗位上的分数会换成上一份评估的；没有上一份时岗位回到「待评估」，可以重新评估。应聘阶段不变。'
          : '删除后找不回来，以后再生成会重新调用模型。'}
      </ConfirmDialog>
    </div>
  )
}
