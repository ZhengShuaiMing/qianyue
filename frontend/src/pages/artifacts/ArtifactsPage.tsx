import { useEffect, type CSSProperties } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router'
import { ARTIFACT_KINDS, exportAllUrl, useArtifact, useArtifacts, type Artifact, type ArtifactKind, type ArtifactSummary } from '../../api/artifacts'
import { useJob } from '../../api/jobs'
import { fmtTime } from '../../lib/format'
import { ArtifactBar } from '../../reports/ArtifactBar'
import type { EvaluationData } from '../../reports/evaluation-data'
import { DocView } from '../../reports/DocView'
import { EvaluationReport } from '../../reports/EvaluationReport'
import { Md } from '../../reports/Md'
import { Button, ButtonDownload, ButtonLink } from '../../ui/Button'
import { Chip } from '../../ui/Chip'
import { EmptyState } from '../../ui/EmptyState'
import { IconArrowRight, IconExport } from '../../ui/icons'
import { Segmented } from '../../ui/Segmented'
import { fmtScore, verdictTint } from '../../lib/verdict'
import './artifacts.css'

type KindKey = ArtifactKind | 'all'
const KINDS: { key: KindKey; label: string }[] = [
  { key: 'all', label: '全部' },
  ...(Object.entries(ARTIFACT_KINDS) as [ArtifactKind, string][]).map(([key, label]) => ({ key, label })),
]

// 每类成果在岗位详情的哪个功能页
const JOB_TAB: Record<ArtifactKind, string> = {
  evaluation: 'evaluation',
  tailored_resume: 'tailoring',
  company_research: 'research',
  communication_advice: 'communication',
  interview_prep: 'interview_prep',
  mock_interview: 'mock',
  profile: 'info', // 能力画像不属于某一个岗位，不显示「岗位详情」
}

// 还没有这类成果时的说明：在哪里发起、得到什么
const EMPTY_TEXT: Record<KindKey, string> = {
  all: '评估报告、定制简历、能力画像等 AI 产出，生成后都会出现在这里。每份可以单独导出成 Markdown 文件，也可以一次全部导出。',
  evaluation: '在岗位详情的「评估」里发起，或者在岗位列表勾选几个一起评估。每份报告包含八个维度的评分和是否建议投递的结论。',
  tailored_resume: '在岗位详情的「定制简历」里发起。你确认过的修改会另存成只给这个岗位用的简历，基础简历不变。',
  company_research: '在岗位详情的「公司研究」里发起。报告整理公司概况、风险与积极信号，每条都注明来源。',
  communication_advice: '在岗位详情的「沟通建议」里发起。根据沟通记录分析对方的意图，并起草回复。',
  interview_prep: '在岗位详情的「面试准备」里发起。预测面试会问的问题，用你的真实经历整理回答要点。',
  mock_interview: '在岗位详情的「模拟面试」里完成一场面试后，逐轮复盘会保存在这里。',
  profile: '在岗位列表点「能力画像」，挑 2–15 个岗位，汇总这类岗位普遍要求的能力和你现在的情况。',
}

/** 标题里带着类型（「岗位评估：……」），列表上类型另有标签，只留后半句 */
const shortTitle = (a: ArtifactSummary) => a.title.replace(/^[^：]{2,8}：/, '') || ARTIFACT_KINDS[a.kind]

/** 成果页：所有 AI 产出。地址里记着选中的成果（/artifacts/3）和类型（?kind=） */
export function ArtifactsPage() {
  const navigate = useNavigate()
  const { artifactId } = useParams()
  const [params, setParams] = useSearchParams()
  const kindParam = params.get('kind') as ArtifactKind | null
  const kind = kindParam && kindParam in ARTIFACT_KINDS ? kindParam : null
  const artifacts = useArtifacts(kind)
  const list = artifacts.data?.pages.flatMap((p) => p.items) ?? []
  const total = artifacts.data?.pages[0]?.total ?? 0
  const wanted = artifactId && /^\d+$/.test(artifactId) ? Number(artifactId) : null
  const selectedId = list.some((a) => a.id === wanted) ? wanted : list[0]?.id ?? null

  const select = (id: number) => navigate({ pathname: `/artifacts/${id}`, search: params.toString() }, { replace: true, preventScrollReset: true })
  const changeKind = (key: KindKey) => setParams(key === 'all' ? {} : { kind: key }, { replace: true, preventScrollReset: true })

  return (
    <div className="artifacts">
      <h1 className="page-title">成果</h1>
      <p className="page-intro">
        <span>这里是迁跃为你做出的全部成果：评估报告、定制简历、公司研究、沟通建议、面试准备、模拟面试复盘和能力画像。</span>
        <span>每一份都只依据你的材料和岗位信息，不会替你编造经历；材料越多、越具体，成果越贴近你本人。</span>
      </p>
      <Segmented items={KINDS} value={kind ?? 'all'} onChange={changeKind} label="按类型筛选" />

      {artifacts.isError ? (
        <div className="artifacts-state">
          <EmptyState title="成果列表没有读出来" action={<Button onClick={() => artifacts.refetch()}>再试一次</Button>}>{artifacts.error.message}</EmptyState>
        </div>
      ) : !artifacts.isPending && list.length === 0 ? (
        <div className="artifacts-state">
          <EmptyState
            title={kind ? `还没有${ARTIFACT_KINDS[kind]}` : '还没有成果'}
            action={<ButtonLink to="/jobs">{kind === 'profile' ? '岗位列表' : '岗位详情'}<IconArrowRight /></ButtonLink>}
          >
            {EMPTY_TEXT[kind ?? 'all']}
          </EmptyState>
        </div>
      ) : (
        <>
          <div className="artifacts-count">
            <h2>{artifacts.isPending ? '正在读取…' : `${total} 份`}<span>最近更新的在前</span></h2>
            <ButtonDownload size="sm" href={exportAllUrl(kind)} title="下载一个 zip，每份成果一个 Markdown 文件；单份成果也能在右边单独导出"><IconExport />{kind ? `导出这 ${total} 份${ARTIFACT_KINDS[kind]}` : `导出全部 ${total} 份`}</ButtonDownload>
          </div>
          <div className="artifacts-split" data-stale={artifacts.isPlaceholderData}>
            <div className="artifacts-list-wrap">
              <ul className="artifacts-list">
                {list.map((a) => (
                  <li key={a.id}>
                    <button type="button" className="artifact-card glass" aria-current={a.id === selectedId} onClick={() => select(a.id)}>
                      <span className="artifact-card-main">
                        <small>{ARTIFACT_KINDS[a.kind]} · {fmtTime(a.updated_at)}</small>
                        <strong>{shortTitle(a)}</strong>
                      </span>
                      {a.kind === 'evaluation' && <span className="artifact-card-score">{fmtScore(a.score)}</span>}
                    </button>
                  </li>
                ))}
              </ul>
              {artifacts.hasNextPage && (
                <div className="artifacts-more">
                  <Button size="sm" onClick={() => artifacts.fetchNextPage()} disabled={artifacts.isFetchingNextPage}>
                    {artifacts.isFetchingNextPage ? '正在读取…' : `再显示 ${Math.min(50, total - list.length)} 份（还有 ${total - list.length} 份）`}
                  </Button>
                </div>
              )}
            </div>
            {selectedId !== null && <ArtifactViewer id={selectedId} onDeleted={() => navigate({ pathname: '/artifacts', search: params.toString() }, { replace: true })} />}
          </div>
        </>
      )}
    </div>
  )
}

function ArtifactViewer({ id, onDeleted }: { id: number; onDeleted: () => void }) {
  const artifact = useArtifact(id)
  const a = artifact.data
  const verdict = a?.kind === 'evaluation' ? (a.data as EvaluationData).scorecard.verdict : null
  useGlow(a ? verdictTint(verdict) : null)
  return (
    <article className="artifact-panel" aria-label="成果内容" style={{ '--tint': verdictTint(verdict) } as CSSProperties}>
      {artifact.isError ? (
        <EmptyState title="这份成果没有读出来" action={<Button onClick={() => artifact.refetch()}>再试一次</Button>}>{artifact.error.message}</EmptyState>
      ) : !a ? (
        <p className="artifacts-loading">正在读取…</p>
      ) : (
        <div className="text-in" key={a.id}>
          <header className="artifact-head">
            <div className="artifact-chips"><Chip>{ARTIFACT_KINDS[a.kind]}</Chip></div>
            <h2>{shortTitle(a)}</h2>
            <JobLink artifact={a} />
          </header>
          <ArtifactBar artifact={a} onDeleted={onDeleted} />
          {a.kind === 'evaluation' ? <EvaluationReport artifact={a} />
            : a.kind === 'tailored_resume' ? <div className="box"><div className="prose"><Md>{a.content_md}</Md></div></div>
              : <DocView md={a.content_md} />}
        </div>
      )}
    </article>
  )
}

/** 去这份成果所属的岗位。岗位删掉了就不显示 */
function JobLink({ artifact }: { artifact: Artifact }) {
  const job = useJob(artifact.job_id)
  if (!artifact.job_id || !job.data) return null
  return <ButtonLink size="sm" to={`/jobs/${artifact.job_id}?tab=${JOB_TAB[artifact.kind]}`}>岗位详情：{job.data.company || job.data.title || '未命名岗位'}<IconArrowRight /></ButtonLink>
}

/** 看评估报告时，背景光带上结论色 */
function useGlow(color: string | null) {
  useEffect(() => {
    const root = document.documentElement.style
    if (color) root.setProperty('--glow', color)
    return () => { root.removeProperty('--glow') }
  }, [color])
}
