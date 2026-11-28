import { useDeleteJob, type Job } from '../../api/jobs'
import { ConfirmDialog } from '../../ui/ConfirmDialog'
import { useToast } from '../../ui/toast'
import { jobName } from './job-look'

export function DeleteJobDialog({ job, onClose, onDeleted }: { job: Job | null; onClose: () => void; onDeleted: () => void }) {
  const remove = useDeleteJob()
  const toast = useToast()
  const close = () => { remove.reset(); onClose() }
  const confirm = () => {
    if (!job) return
    remove.mutate(job.id, {
      onSuccess: () => {
        toast(`已删除「${jobName(job)}」`)
        close()
        onDeleted()
      },
    })
  }
  return (
    <ConfirmDialog
      open={job !== null}
      title={job ? `删除「${jobName(job)}」？` : ''}
      confirmLabel="删除"
      busy={remove.isPending}
      error={remove.isError ? remove.error.message : null}
      onConfirm={confirm}
      onClose={close}
    >
      岗位会从列表里消失，它的评估报告等成果还在「成果」里。以后再添加同一个链接或同一份 JD，会把它恢复回来。
    </ConfirmDialog>
  )
}
