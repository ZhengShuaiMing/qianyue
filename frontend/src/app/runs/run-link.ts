import type { Run, RunKind, RunResult } from '../../api/runs'

// 每种运行的结果在岗位详情的哪个功能页
const TAB: Record<Exclude<RunKind, 'collection' | 'profile'>, string> = {
  evaluation: 'evaluation',
  tailoring: 'tailoring',
  company_research: 'research',
  communication: 'communication',
  interview_prep: 'interview_prep',
  mock_interview: 'mock',
  screenshot: 'communication', // 识别聊天截图存进沟通记录
}

/** 打开一次运行的结果去哪一页。识别岗位截图开始时没有岗位编号，有结论后看结论里存下的岗位 */
export function runLink(run: Run, results: RunResult[] = []): string {
  if (run.kind === 'collection') return '/collect'
  if (run.kind === 'profile') return '/artifacts?kind=profile'
  if (run.kind === 'screenshot' && !run.job_ids.length) {
    const saved = results.find((r) => r.job_id)?.job_id
    return saved ? `/jobs/${saved}` : '/jobs'
  }
  return `/jobs/${run.job_ids[0]}?tab=${TAB[run.kind]}`
}
