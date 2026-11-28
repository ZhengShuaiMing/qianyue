import { Group } from '../ui/Group'
import { Md } from './Md'
import './md.css'
import './report.css'

type Doc = { meta: { key: string; value: string }[]; lead: string; sections: { title: string; md: string }[] }

/** 后端排的报告都是同一个样子：一级标题，几行「**名称:** 内容」，一段引用或说明，然后按二级标题分节。
    拆成这三块分别排版；日期由上面的工具条显示，这里跳过 */
function parseDoc(md: string): Doc {
  const lines = md.replace(/^# .*\n/, '').split('\n')
  const meta: Doc['meta'] = []
  const lead: string[] = []
  let i = 0
  for (; i < lines.length && !lines[i].startsWith('## '); i++) {
    const m = lines[i].match(/^\*\*(.+?):\*\* ?(.*)$/)
    if (m) { if (m[1] !== '日期') meta.push({ key: m[1], value: m[2] }) } else lead.push(lines[i])
  }
  const sections: Doc['sections'] = []
  for (const line of lines.slice(i)) {
    if (line.startsWith('## ')) sections.push({ title: line.slice(3).trim(), md: '' })
    else sections[sections.length - 1].md += line + '\n'
  }
  return { meta, lead: lead.join('\n').trim(), sections }
}

/** 通用的成果视图：头部信息 + 开头一段 + 分组段落 */
export function DocView({ md }: { md: string }) {
  const doc = parseDoc(md)
  return (
    <>
      {doc.meta.length > 0 && (
        <div className="box doc-meta">
          {doc.meta.map((m) => <div key={m.key} className="doc-kv"><span>{m.key}</span><div className="prose"><Md>{m.value}</Md></div></div>)}
        </div>
      )}
      {doc.lead && <div className="doc-lead"><Md>{doc.lead}</Md></div>}
      <div className="doc-sections">
        {doc.sections.map((s, i) => (
          <Group key={`${i}-${s.title}`} label={s.title}><div className="prose"><Md>{s.md.trim()}</Md></div></Group>
        ))}
      </div>
    </>
  )
}
