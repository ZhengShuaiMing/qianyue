import { useEffect, useState, type CSSProperties } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router'
import { useDeleteSearch, useSearch, useSearchHistory, type SearchHit, type SearchRecord, type SearchType } from '../../api/search'
import { fmtTime } from '../../lib/format'
import { Button, ButtonLink } from '../../ui/Button'
import { EmptyState } from '../../ui/EmptyState'
import { SearchField } from '../../ui/SearchField'
import { Group } from '../../ui/Group'
import { IconTrash } from '../../ui/icons'
import { Segmented } from '../../ui/Segmented'
import './search.css'

type TypeKey = SearchType | 'all'
const TYPES: { key: TypeKey; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'material', label: '材料' },
  { key: 'job', label: '岗位' },
  { key: 'communication', label: '沟通' },
  { key: 'artifact', label: '成果' },
  { key: 'chat', label: '助手对话' },
]
// 每类一种颜色，只用在结果前面的小标签上
const TYPE_LOOK: Record<SearchType, { name: string; color: string }> = {
  material: { name: '材料', color: 'var(--sage)' },
  job: { name: '岗位', color: 'var(--teal)' },
  communication: { name: '沟通', color: 'var(--sand)' },
  artifact: { name: '成果', color: 'var(--accent)' },
  chat: { name: '助手对话', color: 'var(--clay)' },
}
const EXAMPLES = ['要 Kafka 的岗位', '做 RAG 的那家公司', '面试准备里的系统设计题', '招聘方约了什么时候电话']

/** 检索页：在材料、岗位、沟通记录、全部成果里一起找。查询和类别记在地址里（?q=&type=），助手和别的页面可以直接带着查询打开 */
export function SearchPage() {
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const typeParam = params.get('type')
  // 「全部」不是一种类型，地址里写 ?type=all 时也按全部找
  const type = typeParam !== 'all' && TYPES.some((t) => t.key === typeParam) ? (typeParam as SearchType) : null
  const [input, setInput] = useState(q)
  const result = useSearch(q, type)
  const go = (next: { q?: string; type?: TypeKey }) => setParams((prev) => {
    const out = new URLSearchParams(prev)
    if (next.q !== undefined) { if (next.q) out.set('q', next.q); else out.delete('q') }
    if (next.type !== undefined) { if (next.type === 'all') out.delete('type'); else out.set('type', next.type) }
    return out
  }, { replace: true })
  const ask = `在迁跃里查一下「${q}」，根据找到的内容回答我，说明每条出自哪里。`
  const history = useSearchHistory()
  const client = useQueryClient()
  // 每查完一次，右边的历史跟着更新
  useEffect(() => { if (result.dataUpdatedAt) void client.invalidateQueries({ queryKey: ['search-history'] }) }, [result.dataUpdatedAt, client])
  // 框里的字删光（或点了清空）就收起结果，回到「试试」
  const edit = (value: string) => {
    setInput(value)
    if (!value.trim() && q) go({ q: '' })
  }
  const open = (record: SearchRecord) => { setInput(record.query); go({ q: record.query, type: record.type ?? 'all' }) }

  return (
    <div className="search-page">
      <h1 className="page-title">检索</h1>
      <p className="page-intro">
        <span>在你的材料、岗位、沟通记录、全部成果和助手对话里一起找：同时按意思和按字面查找，合并后重新排序。</span>
        <span>助手回答「哪些岗位要 Kafka」这类问题时，会先在这里查找，再根据查到的内容回答，这是迁跃的 RAG（检索增强生成）。</span>
      </p>
      <div className="search-split">
      <section className="search-main">
      <form className="search-row" onSubmit={(e) => { e.preventDefault(); go({ q: input.trim() }) }}>
        <div className="search-row-field"><SearchField value={input} onChange={edit} placeholder="比如：要 Kafka 的岗位、做 RAG 的那家公司" label="检索" /></div>
        <Button type="submit" size="lg" variant="primary" disabled={!input.trim() || result.isFetching}>{result.isFetching ? '正在检索…' : '检索'}</Button>
      </form>
      <div className="search-filter">
        <Segmented items={TYPES} value={type ?? 'all'} onChange={(key) => go({ type: key })} label="只在这一类里找" />
        {q && result.data && result.data.length > 0 && (
          <ButtonLink to={`/assistant?say=${encodeURIComponent(ask)}`}>让助手根据这些回答</ButtonLink>
        )}
      </div>

      {!q ? (
        <div className="search-examples">
          <span>试试：</span>
          {EXAMPLES.map((example) => (
            <button key={example} type="button" onClick={() => { setInput(example); go({ q: example }) }}>{example}</button>
          ))}
        </div>
      ) : result.isError ? (
        <EmptyState title="检索没有成功" action={<Button onClick={() => result.refetch()}>再试一次</Button>}>{result.error.message}</EmptyState>
      ) : !result.data ? (
        <p className="search-note"><span className="spinner" />正在检索。首次检索需要先为全部内容建立索引，内容较多时约需 1–2 分钟。</p>
      ) : result.data.length === 0 ? (
        <EmptyState title="没有找到相关的内容">{type ? '换个说法，或切换到「全部」查找。' : '换个说法再试一次。'}</EmptyState>
      ) : (
        <ol className="search-hits" data-stale={result.isFetching}>
          {result.data.map((hit, i) => <Hit key={i} hit={hit} />)}
        </ol>
      )}
      </section>
      <History records={history.data ?? []} current={{ q, type }} onOpen={open} />
      </div>
    </div>
  )
}

/** 右边的检索历史：点一条重新查，可以删一条或全部清空 */
function History({ records, current, onOpen }: { records: SearchRecord[]; current: { q: string; type: SearchType | null }; onOpen: (record: SearchRecord) => void }) {
  const remove = useDeleteSearch()
  return (
    <aside className="search-side">
      <Group
        label="检索历史"
        plain={records.length === 0}
        hint={records.length ? `最近 ${records.length} 条` : undefined}
        action={records.length > 1 ? <Button size="sm" onClick={() => remove.mutate(null)} disabled={remove.isPending}>全部清空</Button> : undefined}
      >
        {records.length === 0 ? <p className="search-side-empty">检索过的内容会记在这里，点一条可以重新查看。</p> : records.map((r) => (
          <div key={r.id} className="search-record" aria-current={r.query === current.q && r.type === current.type}>
            <button type="button" onClick={() => onOpen(r)}>
              <strong>{r.query}</strong>
              <small>{TYPES.find((t) => t.key === (r.type ?? 'all'))?.label} · {r.hits} 条 · {fmtTime(r.created_at)}</small>
            </button>
            <button type="button" className="search-record-remove" aria-label={`删除「${r.query}」这条记录`} title="删除这条记录" onClick={() => remove.mutate(r.id)} disabled={remove.isPending}><IconTrash /></button>
          </div>
        ))}
      </Group>
    </aside>
  )
}

function Hit({ hit }: { hit: SearchHit }) {
  const look = TYPE_LOOK[hit.type]
  return (
    <li>
      <Link className="search-hit box" to={hit.link}>
        <div className="search-hit-head">
          <span className="search-tag" style={{ '--c': look.color } as CSSProperties}>{look.name}</span>
          <strong>{hit.title}</strong>
          {!hit.literal && <span className="search-near" title="字面上没有和查询相同的地方，是按意思找到的">意思相近</span>}
          <time dateTime={hit.date}>{fmtTime(hit.date)}</time>
        </div>
        {hit.heading && <p className="search-hit-heading">{hit.heading}</p>}
        {hit.text && <p className="search-hit-text"><Marked text={hit.text} spans={hit.highlights} /></p>}
      </Link>
    </li>
  )
}

/** 把和查询字面相同的地方加亮 */
function Marked({ text, spans }: { text: string; spans: [number, number][] }) {
  const parts = []
  let at = 0
  for (const [start, end] of spans) {
    if (start > at) parts.push(text.slice(at, start))
    parts.push(<mark key={start}>{text.slice(start, end)}</mark>)
    at = end
  }
  parts.push(text.slice(at))
  return <>{parts}</>
}
