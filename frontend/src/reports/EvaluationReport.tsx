import { useState, type CSSProperties } from 'react'
import { useNavigate } from 'react-router'
import type { Artifact } from '../api/artifacts'
import { Button } from '../ui/Button'
import { Chip } from '../ui/Chip'
import { Group } from '../ui/Group'
import { IconChevron } from '../ui/icons'
import { CONFIDENCE, type EvaluationData } from './evaluation-data'
import { Md } from './Md'
import './md.css'
import './report.css'

const TABS = [
  { key: 'score', label: '评分明细' },
  { key: 'overview', label: '岗位概览' },
  { key: 'match', label: '匹配分析' },
  { key: 'plan', label: '定制计划' },
] as const
type Tab = (typeof TABS)[number]['key']

/** 评估报告：分数和结论一直在上面，下面按主题分四页。颜色用外层给的 --tint（结论色） */
export function EvaluationReport({ artifact }: { artifact: Artifact }) {
  const { output, scorecard, recheck_totals } = artifact.data as EvaluationData
  const [tab, setTab] = useState<Tab>('score')
  const blockers = scorecard.confirmed_blockers ? `已确认 ${scorecard.confirmed_blockers} 项`
    : scorecard.pending_blockers ? `待核实 ${scorecard.pending_blockers} 项` : '无'
  return (
    <>
      <section className="report-score-row">
        <p className="report-score"><span>{scorecard.total.toFixed(1)}</span><small>/ 5</small></p>
        <div className="report-stats">
          <span className="report-stat"><small>信息覆盖率</small>{scorecard.coverage}%</span>
          <span className="report-stat"><small>置信度</small>{CONFIDENCE[scorecard.confidence]}</span>
          <span className="report-stat" data-alert={scorecard.confirmed_blockers > 0}><small>硬性阻断</small>{blockers}</span>
        </div>
      </section>
      <p className="report-summary">{output.summary}</p>
      {recheck_totals.length > 0 && (
        <p className="report-note">总分落在结论的分数线附近，一共评了 {recheck_totals.length} 次，每个维度取中间值（各次总分：{recheck_totals.join('、')}）。</p>
      )}

      <div className="report-tabs" role="tablist" aria-label="报告分页">
        {TABS.map((t) => (
          <button key={t.key} type="button" role="tab" aria-selected={tab === t.key} onClick={() => setTab(t.key)}>{t.label}</button>
        ))}
      </div>
      <div className="report-tab-body" key={tab}>
        {tab === 'score' && <ScoreTab artifact={artifact} />}
        {tab === 'overview' && (
          <>
            <Prose title={output.direction ? `岗位概览 · ${output.direction}` : '岗位概览'} text={output.a_overview} />
            {output.keywords.length > 0 && (
              <Group label="关键词" plain>
                <div className="report-keywords">{output.keywords.map((k) => <Chip key={k}>{k}</Chip>)}</div>
              </Group>
            )}
          </>
        )}
        {tab === 'match' && (
          <>
            <Prose title="简历匹配" text={output.b_resume_match} />
            <Prose title="级别与定位" text={output.c_level} />
            <Prose title="薪资与市场" text={output.d_compensation} />
          </>
        )}
        {tab === 'plan' && <Prose title="怎么改简历" text={output.e_plan} />}
      </div>
    </>
  )
}

function Prose({ title, text }: { title: string; text: string }) {
  return <Group label={title}><div className="prose"><Md>{text}</Md></div></Group>
}

function ScoreTab({ artifact }: { artifact: Artifact }) {
  const { output } = artifact.data as EvaluationData
  const navigate = useNavigate()
  const [open, setOpen] = useState<string | null>(null)
  const infos = artifact.dimensions ?? []
  const rows = infos.map((info) => ({ ...info, ...output.dimensions[info.key] }))
  // 这次没计分、但补上信息就能计分的维度
  const missing = rows.filter((d) => d.score === null && d.how_to_score)
  return (
    <>
      {missing.length > 0 && (
        <Group label="怎样让这份评估更准" hint="这几项这次没有计分，补上信息后重新评估">
          {missing.map((item) => (
            <div key={item.key} className="report-row">
              <div><strong>{item.name}<small>（权重 {item.weight}%）</small></strong><span>{item.how_to_score}</span></div>
              <Button size="sm" onClick={() => navigate('/materials')}>去「材料」页补充</Button>
            </div>
          ))}
        </Group>
      )}
      {output.blockers.length > 0 && (
        <Group label="硬性阻断" hint="不满足就不建议投；分数照算，结论单独处理">
          {output.blockers.map((b) => (
            <div key={b.issue} className="report-blocker" data-status={b.status}>
              <strong><span>{b.status === 'confirmed' ? '已确认' : '待核实'}</span>{b.issue}</strong>
              <p>{b.evidence}</p>
            </div>
          ))}
        </Group>
      )}
      <Group label="八个维度" hint="点一行看依据">
        {rows.map((d) => {
          const isOpen = open === d.key
          return (
            <div key={d.key} className="dim" data-open={isOpen}>
              <button type="button" className="dim-head" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : d.key)}>
                <span className="dim-name">{d.name}<small>{d.weight}%</small></span>
                <span className="dim-track" data-na={d.score === null}><span style={{ '--w': (d.score ?? 0) / 5 } as CSSProperties} /></span>
                <span className="dim-score">{d.score ?? '不可评估'}</span>
                <IconChevron />
              </button>
              <div className="dim-body"><p>{d.evidence}</p></div>
            </div>
          )
        })}
      </Group>
      {output.key_unknowns.length > 0 && (
        <Group label="待确认" hint="面试或沟通时可以问">
          <div className="prose"><ul>{output.key_unknowns.map((u) => <li key={u}>{u}</li>)}</ul></div>
        </Group>
      )}
    </>
  )
}
