import { useEffect, useRef, useState, type ClipboardEvent, type DragEvent } from 'react'
import { useToast } from './toast'

export type Shot = { id: string; url: string; file: File }

const asDataUrl = (file: File) => new Promise<string>((resolve, reject) => {
  const reader = new FileReader()
  reader.onload = () => resolve(String(reader.result))
  reader.onerror = () => reject(reader.error)
  reader.readAsDataURL(file)
})

/** 一组待发的截图：粘贴、拖入、选择都进这里。不限张数和大小，模型收不了时由它报错。
    截图没法脱敏，原样发给 AI（模型或你的 Agent），设置页的隐私说明和使用指南里写了先遮挡个人信息。
    area 挂在接收粘贴和拖入的那一块上；input 是隐藏的选择框，pick() 打开它 */
export function useScreenshots(onChange?: () => void) {
  const [shots, setShots] = useState<Shot[]>([])
  const [dragging, setDragging] = useState(false)
  const picker = useRef<HTMLInputElement>(null)
  const toast = useToast()

  // 卸载时释放预览图
  const latest = useRef(shots)
  useEffect(() => { latest.current = shots }, [shots])
  useEffect(() => () => latest.current.forEach((s) => URL.revokeObjectURL(s.url)), [])

  const add = (files: File[]) => {
    const images = files.filter((f) => f.type.startsWith('image/'))
    if (!images.length) return
    onChange?.()
    setShots((all) => [...all, ...images.map((file) => ({ id: crypto.randomUUID(), url: URL.createObjectURL(file), file }))])
  }
  const remove = (id: string) => {
    const gone = shots.find((s) => s.id === id)
    if (gone) URL.revokeObjectURL(gone.url)
    setShots((all) => all.filter((s) => s.id !== id))
  }
  const clear = () => { shots.forEach((s) => URL.revokeObjectURL(s.url)); setShots([]) }

  /** 读成 data:image/...;base64,... 交给接口。读不出来时提示并返回 null，草稿和截图都留着 */
  const dataUrls = async (): Promise<string[] | null> => {
    try {
      return await Promise.all(shots.map((s) => asDataUrl(s.file)))
    } catch {
      toast('截图没有读出来，请移除后重新添加。', 'error')
      return null
    }
  }

  return {
    shots,
    dragging,
    add,
    remove,
    clear,
    pick: () => picker.current?.click(),
    dataUrls,
    area: {
      'data-dragging': dragging,
      onPaste: (e: ClipboardEvent) => {
        const files = [...e.clipboardData.files]
        if (files.some((f) => f.type.startsWith('image/'))) { e.preventDefault(); add(files) }
      },
      onDragOver: (e: DragEvent) => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); setDragging(true) } },
      onDragLeave: (e: DragEvent) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false) },
      onDrop: (e: DragEvent) => { if (e.dataTransfer.files.length) { e.preventDefault(); setDragging(false); add([...e.dataTransfer.files]) } },
    },
    input: (
      <input ref={picker} type="file" accept="image/*" multiple hidden onChange={(e) => { add([...(e.target.files ?? [])]); e.target.value = '' }} />
    ),
  }
}

