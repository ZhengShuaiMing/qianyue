import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { useDocument, useSaveDocument, useVersion, useVersions, type DocumentKind } from '../../api/documents'
import { fmtTime } from '../../lib/format'
import { Md } from '../../reports/Md'
import { Button } from '../../ui/Button'
import { Dialog } from '../../ui/Dialog'
import { EmptyState } from '../../ui/EmptyState'
import { TextArea } from '../../ui/Field'
import { Segmented } from '../../ui/Segmented'
import { useToast } from '../../ui/toast'
import './materials.css'

type Kind = { key: DocumentKind; label: string; hint: string; empty: string; sample: string; ask: string }
// 顺序：必填的基础简历在前，STAR 经历是简历的展开，补充资料放最后
const KINDS: Kind[] = [
  {
    key: 'resume', label: '基础简历', ask: '帮我一起整理基础简历：先看看我现在的简历，再一项一项问我缺什么。',
    hint: '必填。你的基础简历是岗位评估、定制简历、面试准备等功能的依据。定制简历不会改动基础简历，而是另存一份。',
    empty: '已有简历可以点「手动填写」后直接粘贴，也可以点「和助手一起写」，在对话中逐项整理。',
    sample: '# 张三\n上海 · 5 年后端经验 · zhangsan@example.com\n\n## 工作经历\n\n### 某某科技 · 后端工程师（2021.07 – 至今）\n- 负责什么，用了哪些技术\n- 做成了什么，最好带数字：上线后接口耗时降低 40%\n\n## 项目经历\n\n### 项目名称\n- 你的角色和具体做的事\n- 结果\n\n## 技能\n\nPython、FastAPI、Redis……\n\n## 教育\n\n某某大学 · 计算机科学 · 本科（2019）',
  },
  {
    key: 'star', label: 'STAR 经历', ask: '帮我把一段经历按 STAR 方式整理，保存到「STAR 经历」。',
    hint: '选填。把做过的事按情境、任务、行动、结果展开写，补充简历里写不下的细节。',
    empty: '一段经历一个标题，写清当时的情况、你要做的事、你怎么做的、结果如何。也可以讲给助手听，它帮你整理。',
    sample: '## 客服助手项目\n\n- 情境：当时团队遇到什么问题\n- 任务：你要负责解决什么\n- 行动：你具体做了哪几件事\n- 结果：最后怎么样，有数字更好\n\n## 下一段经历的名称\n\n- 情境：……',
  },
  {
    key: 'supplement', label: '补充资料', ask: '帮我一起写补充资料：求职方向、期望城市和月薪、能不能出差、到岗时间这些，一项一项问我。',
    hint: '选填。补充你的求职方向和期望、能否出差、到岗时间，以及作品和证书。经历写在基础简历或 STAR 经历里。',
    empty: '比如想找什么方向、期望城市和月薪、能不能出差、什么时候能到岗，以及作品链接和证书。',
    sample: '- 求职方向：……\n- 期望城市：……\n- 期望月薪：……\n- 能否出差或驻场：……\n- 到岗时间：……\n- 作品或链接：……\n- 证书：……',
  },
]

/** 材料页：左边是三份材料的查看、编辑和历史版本，右边是参考写法。地址里记着当前分页（?kind=） */
export function MaterialsPage() {
  const [params, setParams] = useSearchParams()
  const kindParam = params.get('kind')
  const kind = KINDS.find((k) => k.key === kindParam) ?? KINDS[0]
  // 三份材料一进页面就都读好，切换分页时直接显示，不先闪一下「正在读取」
  useDocument('resume'); useDocument('supplement'); useDocument('star')
  // 没保存的编辑按材料分别留着，切换分页不丢
  const [drafts, setDrafts] = useState<Partial<Record<DocumentKind, string>>>({})
  const dirty = Object.keys(drafts).length > 0
  useEffect(() => {
    if (!dirty) return
    const warn = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])
  const setDraft = (k: DocumentKind, value: string | null) => setDrafts((all) => {
    const next = { ...all }
    if (value === null) delete next[k]; else next[k] = value
    return next
  })

  return (
    <div className="materials">
      <h1 className="page-title">我的材料</h1>
      <p className="page-intro">
        <span>这些材料是迁跃了解你的依据。评估、定制简历、面试准备只用这里写的真实经历，不会替你编造。</span>
        <span>材料越多、越具体，评估越准，定制和面试准备也越贴近你本人。</span>
      </p>
      <Segmented
        items={KINDS.map((k) => ({ key: k.key, label: <>{k.label}{drafts[k.key] !== undefined && <i className="materials-dirty" title="有未保存的修改" />}</> }))}
        value={kind.key}
        onChange={(key) => setParams(key === 'resume' ? {} : { kind: key }, { replace: true })}
        label="材料"
      />
      <div className="materials-split">
        <DocumentPanel key={kind.key} kind={kind} draft={drafts[kind.key]} onDraft={(v) => setDraft(kind.key, v)} />
        <Sample kind={kind} canUse={drafts[kind.key] !== undefined && !drafts[kind.key]!.trim()} onUse={() => setDraft(kind.key, kind.sample)} />
      </div>
    </div>
  )
}

function DocumentPanel({ kind, draft, onDraft }: { kind: Kind; draft: string | undefined; onDraft: (value: string | null) => void }) {
  const doc = useDocument(kind.key)
  const save = useSaveDocument(kind.key)
  const toast = useToast()
  const navigate = useNavigate()
  const editing = draft !== undefined
  const askAssistant = () => navigate(`/assistant?say=${encodeURIComponent(kind.ask)}`)
  const start = () => { save.reset(); onDraft(doc.data?.content_md ?? '') }
  const submit = () => {
    if (!draft?.trim() || save.isPending) return
    // 用 mutateAsync 的结果收尾：保存途中切到别的页签时，这个面板已经卸载，mutate 的回调不会执行，
    // 草稿就清不掉，红点一直留着。失败的原因由 save.error 显示在面板上
    save.mutateAsync(draft).then(
      ({ created }) => { toast(created ? `${kind.label}已保存为新版本` : '内容没有变化，未新增版本'); onDraft(null) },
      () => {},
    )
  }

  let body
  if (doc.isError) body = <EmptyState title={`${kind.label}没有读出来`} action={<Button onClick={() => doc.refetch()}>再试一次</Button>}>{doc.error.message}</EmptyState>
  else if (doc.isPending) body = <p className="materials-loading">正在读取…</p>
  else if (editing) {
    body = (
      <div className="materials-edit">
        <TextArea value={draft} onChange={(e) => onDraft(e.target.value)} placeholder="粘贴已有的内容，或者照着参考写法写" autoFocus aria-label={kind.label} />
        <div className="materials-edit-bar">
          <span data-tone={save.isError ? 'error' : undefined}>{save.isError ? `没有保存成功：${save.error.message}` : `${draft.trim().length} 字`}</span>
          <Button size="sm" onClick={() => { save.reset(); onDraft(null) }}>取消</Button>
          <Button size="sm" variant="primary" onClick={submit} disabled={!draft.trim() || save.isPending}>{save.isPending ? '正在保存…' : '保存为新版本'}</Button>
        </div>
      </div>
    )
  } else if (!doc.data) {
    body = (
      <EmptyState title={`还没有${/^[A-Za-z]/.test(kind.label) ? ' ' : ''}${kind.label}`} action={<div className="materials-start"><Button variant="primary" onClick={start}>手动填写</Button><Button onClick={askAssistant}>和助手一起写</Button></div>}>
        {kind.empty}
      </EmptyState>
    )
  } else {
    body = <div className="box"><div className="prose"><Md>{doc.data.content_md}</Md></div></div>
  }

  return (
    <section className="materials-doc">
      <div>
        <div className="materials-doc-head">
          <p>{kind.hint}</p>
          {!editing && <Versions kind={kind.key} />}
          {doc.data && !editing && <Button size="sm" onClick={askAssistant}>和助手一起写</Button>}
          {doc.data && !editing && <Button size="sm" onClick={start}>编辑</Button>}
        </div>
      </div>
      {body}
    </section>
  )
}

/** 历史版本：材料标题旁的按钮，点开是版本列表（最新的在前）。点一个看内容，可以把它恢复成最新版本（另存一版，不删别的） */
function Versions({ kind }: { kind: DocumentKind }) {
  const versions = useVersions(kind)
  const [open, setOpen] = useState(false)
  const [viewing, setViewing] = useState<number | null>(null)
  const list = versions.data ?? []
  if (list.length < 2) return null
  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)}>历史版本 {list.length}</Button>
      {open && viewing === null && (
        <Dialog open onClose={() => setOpen(false)} title="历史版本" actions={<Button onClick={() => setOpen(false)}>关闭</Button>}>
          <div className="box">
            {list.map((v, i) => (
              <button key={v.id} type="button" className="materials-version" onClick={() => setViewing(v.id)}>
                <span>{fmtTime(v.created_at)}</span>
                {v.is_current ? <small>当前</small> : <small>第 {list.length - i} 版</small>}
              </button>
            ))}
          </div>
        </Dialog>
      )}
      {viewing !== null && <VersionDialog kind={kind} id={viewing} onClose={() => setViewing(null)} />}
    </>
  )
}

function VersionDialog({ kind, id, onClose }: { kind: DocumentKind; id: number; onClose: () => void }) {
  const version = useVersion(kind, id)
  const save = useSaveDocument(kind)
  const toast = useToast()
  const v = version.data
  const restore = () => v && save.mutate(v.content_md, { onSuccess: ({ created }) => { toast(created ? '已恢复成最新版本' : '与当前版本相同，未新增版本'); onClose() } })
  return (
    <Dialog
      open
      onClose={onClose}
      size="wide"
      title={v ? `${fmtTime(v.created_at)} 的版本` : '历史版本'}
      actions={
        <>
          <Button onClick={onClose}>关闭</Button>
          {v && !v.is_current && <Button variant="primary" onClick={restore} disabled={save.isPending}>{save.isPending ? '正在恢复…' : '恢复这一版'}</Button>}
        </>
      }
    >
      {v ? <div className="prose materials-version-body"><Md>{v.content_md}</Md></div> : <p className="dialog-text">{version.isError ? version.error.message : '正在读取…'}</p>}
      {v && !v.is_current && <p className="dialog-text">恢复会把这一版另存为最新版本，其他版本都还在。</p>}
      {save.isError && <p className="dialog-text" data-tone="error">没有恢复：{save.error.message}</p>}
    </Dialog>
  )
}

/** 右边的参考写法：一直看得到；编辑时还没写东西，可以拿它当开头。
    框的上沿和左边输入框对齐，下沿不超过左边输入框，内容多了在框里滚 */
function Sample({ kind, canUse, onUse }: { kind: Kind; canUse: boolean; onUse: () => void }) {
  return (
    <aside className="materials-sample-col">
      <div className="group-head">
        <h3>参考写法</h3>
        {canUse && <Button size="sm" onClick={onUse}>套用参考写法</Button>}
      </div>
      <div className="materials-sample-wrap">
        <pre className="box materials-sample">{kind.sample}</pre>
      </div>
    </aside>
  )
}
