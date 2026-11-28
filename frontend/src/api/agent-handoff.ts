import type { QueryClient } from '@tanstack/react-query'
import type { Health } from './health'
import type { JobDetail } from './jobs'
import type { Run, RunKind, StartRun } from './runs'

/** 没有配置模型 Key 时，要用 AI 的事交给用户自己的 Agent（经 MCP 由它代答）。
    各入口不用自己画弹窗：发这个事件，外壳里的弹窗给出一句可以复制的话，并跟着看 Agent 做到哪了 */
export const AGENT_HANDOFF_EVENT = 'qianyue:agent-handoff'

/** 弹窗跟着看哪次运行：迁跃已经发起、等 Agent 接手的（识别截图），
    或者 Agent 收到这句话后才会发起的（评估等），按类型和岗位在任务列表里认出来 */
export type HandoffWatch =
  | { run: string }
  | { kind: RunKind; jobIds: number[]; since: number }

export type Handoff = {
  /** 这一步是什么，弹窗第一句用 */
  what: string
  sentence: string
  watch?: HandoffWatch
  /** 复制成功后调用（例如助手清空输入框） */
  onCopied?: () => void
}

export function openAgentHandoff(handoff: Handoff) {
  window.dispatchEvent(new CustomEvent(AGENT_HANDOFF_EVENT, { detail: handoff }))
}

/** 有没有配置模型 Key。健康检查还没回来时按有 Key 处理，后端会给出原因 */
export function hasNoKey(client: QueryClient): boolean {
  return client.getQueryData<Health>(['health'])?.llm_configured === false
}

/** 采集不需要模型，其余任务都要 */
export function needsAgent(client: QueryClient, body: StartRun): boolean {
  return body.kind !== 'collection' && hasNoKey(client)
}

type AgentKind = Exclude<RunKind, 'collection' | 'screenshot'>

const TASK: Record<AgentKind, [string, (jobs: string) => string]> = {
  evaluation: ['评估岗位', (jobs) => `用迁跃评估岗位 ${jobs}`],
  profile: ['汇总能力画像', (jobs) => `用迁跃把岗位 ${jobs} 汇总成能力画像`],
  tailoring: ['定制简历', (jobs) => `用迁跃为岗位 ${jobs} 定制简历`],
  company_research: ['研究公司', (jobs) => `用迁跃研究岗位 ${jobs} 的公司`],
  communication: ['写沟通建议', (jobs) => `用迁跃给岗位 ${jobs} 写沟通建议`],
  interview_prep: ['准备面试', (jobs) => `用迁跃准备岗位 ${jobs} 的面试`],
  mock_interview: ['模拟面试', (jobs) => `用迁跃和我模拟面试岗位 ${jobs}`],
}

/** 发起评估等任务：岗位编号是迁跃认的，公司和职位名让用户和 Agent 都一眼看清是哪个 */
export function taskHandoff(client: QueryClient, body: StartRun): Handoff {
  const [what, sentenceFor] = TASK[body.kind as AgentKind]
  const jobs = body.job_ids.length === 1
    ? named(client, body.job_ids[0])
    : body.job_ids.map((id) => `#${id}`).join('、')
  let sentence = sentenceFor(jobs)
  if (body.conditions?.trim()) sentence += `，这次的要求：${body.conditions.trim()}`
  if (body.force) sentence += '（重新做一次）'
  return { what, sentence, watch: { kind: body.kind, jobIds: body.job_ids, since: Date.now() } }
}

/** 带截图添加岗位或沟通：截图已经存进迁跃、运行已经发起，Agent 按运行编号接手 */
export function screenshotHandoff(run: Run, target: 'job' | 'communication', client: QueryClient): Handoff {
  const what = target === 'job' ? '识别岗位截图' : '识别聊天截图'
  const into = target === 'job' ? '存成岗位' : `存进岗位 ${named(client, run.job_ids[0])}的沟通记录`
  return { what, sentence: `用迁跃识别我刚上传的截图，${into}（运行编号 ${run.id}）`, watch: { run: run.id } }
}

/** 助手：没有 Key 时由用户自己的 Agent 当助手。截图已经存进迁跃时带上截图编号 */
export function assistantHandoff(text: string, upload?: { id: string; count: number }, onCopied?: () => void): Handoff {
  const shots = upload ? `我在迁跃里上传了 ${upload.count} 张截图（截图编号 ${upload.id}）` : ''
  const sentence = text
    ? `用迁跃：${text}${shots ? `\n${shots}` : ''}`
    : `用迁跃看一下${shots.replace(/^我在迁跃里上传了/, '我上传的')}，告诉我可以怎么处理`
  return { what: '助手回复', sentence, onCopied }
}

function named(client: QueryClient, id: number): string {
  const job = client.getQueryData<JobDetail>(['jobs', 'detail', id])
  const name = job ? [job.company, job.title].filter(Boolean).join(' · ') : ''
  return name ? `#${id}「${name}」` : `#${id}`
}
