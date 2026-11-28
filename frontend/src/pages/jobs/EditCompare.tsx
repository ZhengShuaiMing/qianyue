import { useState } from 'react'
import { Button } from '../../ui/Button'
import { TextArea } from '../../ui/Field'
import { diffText, type Piece } from './text-diff'

type Props = {
  kind: 'replace' | 'delete' | 'insert' | 'move'
  original: string
  /** 现在要用的新内容：你改过就是你写的，否则是方案里的 */
  text: string
  edited: boolean
  /** 不传就只能看，不能改（例如已经生成的定制简历） */
  editing?: boolean
  onEdit?: () => void
  onDone?: (text: string) => void
  onCancel?: () => void
  onRestore?: () => void
}

function Pieces({ pieces, mark }: { pieces: Piece[]; mark: 'del' | 'ins' }) {
  const Tag = mark
  return <p className="tailor-text">{pieces.map((p, i) => (p.changed ? <Tag key={i}>{p.text}</Tag> : <span key={i}>{p.text}</span>))}</p>
}

/** 一条修改的左右对照：左边原文，删掉的字划线；右边新内容，新加的字高亮。右边可以点「改一下」直接改 */
export function EditCompare({ kind, original, text, edited, editing, onEdit, onDone, onCancel, onRestore }: Props) {
  const hasNew = kind === 'replace' || kind === 'insert'
  const diff = kind === 'replace' ? diffText(original, text) : null
  const before: Piece[] = diff?.before ?? [{ text: original, changed: kind === 'delete' }]
  const after: Piece[] = diff?.after ?? [{ text, changed: true }]
  const oldLabel = kind === 'move' ? '要移动的内容' : kind === 'delete' ? '要删掉的内容' : '原文'

  return (
    <div className="tailor-compare" data-cols={original && hasNew ? 2 : 1}>
      {original && (
        <div className="tailor-diff" data-side="old">
          <small>{oldLabel}</small>
          <Pieces pieces={before} mark="del" />
        </div>
      )}
      {hasNew && (
        <div className="tailor-diff" data-side="new">
          <div className="tailor-diff-head">
            <small>{kind === 'insert' ? '插入的内容' : '改为'}</small>
            {onEdit && !editing && (
              <span className="tailor-diff-actions">
                {edited && <button type="button" className="tailor-link" onClick={onRestore}>恢复方案原文</button>}
                <button type="button" className="tailor-link" onClick={onEdit}>编辑</button>
              </span>
            )}
          </div>
          {editing ? <Editor initial={text} onDone={onDone!} onCancel={onCancel!} /> : <Pieces pieces={after} mark="ins" />}
        </div>
      )}
    </div>
  )
}

function Editor({ initial, onDone, onCancel }: { initial: string; onDone: (text: string) => void; onCancel: () => void }) {
  const [draft, setDraft] = useState(initial)
  const empty = !draft.trim()
  return (
    <div className="tailor-editor">
      <TextArea
        autoFocus
        size="short"
        aria-label="改这条修改的新内容"
        value={draft}
        rows={Math.min(10, Math.max(3, draft.split('\n').length + 1))}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Escape') onCancel() }}
      />
      <div className="tailor-editor-bar">
        <span>{empty ? '内容不能为空。不需要这条修改时，请取消勾选' : '将按你填写的内容原样使用，不再与材料核对'}</span>
        <Button size="sm" onClick={onCancel}>取消</Button>
        <Button size="sm" variant="primary" disabled={empty} onClick={() => onDone(draft)}>完成</Button>
      </div>
    </div>
  )
}
