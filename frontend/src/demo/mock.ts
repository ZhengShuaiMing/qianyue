// 只读快照的「后端」：在线演示版和本地版的示例数据，所有请求都从 snapshot.json 里应答，快照由
// scripts/export-demo.mjs 导出。只能读：改数据、运行 AI 的请求一律回一句说明。只在快照模式下才加载（demo/mode.ts）
import { ApiError, DEMO_BLOCKED } from '../api/client'
import type { ArtifactSummary } from '../api/artifacts'
import type { Job } from '../api/jobs'

type Snapshot = {
  searches: string[]
  /** 「类别|排序」→ 岗位编号，顺序和后端一致 */
  jobLists: Record<string, number[]>
  jobItems: Record<string, Job>
  artifacts: ArtifactSummary[]
  /** 请求路径（不带 /api）→ 后端当时的返回 */
  responses: Record<string, unknown>
}

const NAME = __DEMO__ ? '演示版' : '示例数据'
let snapshot: Promise<Snapshot> | null = null
const load = () => (snapshot ??= import('./snapshot.json').then((m) => m.default as unknown as Snapshot))

export async function demoApi(path: string, init?: RequestInit): Promise<{ status: number; data: unknown }> {
  if ((init?.method ?? 'GET').toUpperCase() !== 'GET') throw new ApiError(403, DEMO_BLOCKED)
  const s = await load()
  const [pathname, query = ''] = path.split('?')
  const params = new URLSearchParams(query)
  if (pathname === '/jobs') return ok(page(jobs(s, params), params))
  if (pathname === '/artifacts') return ok(page(artifacts(s, params), params))
  if (path in s.responses) return ok(s.responses[path])
  if (pathname === '/search') throw new ApiError(404, `${NAME}不能实时检索，可以试试这几个词：${s.searches.join('、')}`)
  throw new ApiError(404, `${NAME}里没有这项内容`)
}

const ok = (data: unknown) => ({ status: 200, data })

function page<T>(items: T[], params: URLSearchParams) {
  const offset = Number(params.get('offset') ?? 0)
  const limit = Number(params.get('limit') ?? 50)
  return { items: items.slice(offset, offset + limit), total: items.length }
}

/** 和后端一样：按类别和排序取顺序，再按公司或岗位名包含关键词筛 */
function jobs(s: Snapshot, params: URLSearchParams): Job[] {
  const ids = s.jobLists[`${params.get('category') ?? ''}|${params.get('sort') ?? 'recent'}`] ?? []
  const q = params.get('q')?.trim().toLowerCase()
  return ids
    .map((id) => s.jobItems[id])
    .filter((j) => !q || j.company.toLowerCase().includes(q) || j.title.toLowerCase().includes(q))
}

function artifacts(s: Snapshot, params: URLSearchParams): ArtifactSummary[] {
  const kind = params.get('kind')
  const jobId = params.get('job_id')
  return s.artifacts.filter((a) => (!kind || a.kind === kind) && (!jobId || a.job_id === Number(jobId)))
}
