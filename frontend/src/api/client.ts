import { SNAPSHOT } from '../demo/mode'

/** 后端返回的错误：FastAPI 把原因放在 detail 里 */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/** 只读快照里改数据、运行 AI、导出时的说明：在线演示版，或本地版的示例数据 */
export const DEMO_BLOCKED = __DEMO__
  ? '这是在线演示，只能浏览。下载迁跃在本机运行，就能用全部功能'
  : '这是示例数据，只能浏览。退出示例后就能用你自己的数据'

/** 调后端接口，同时给出状态码（有的接口用 200 和 201 区分「已存在」和「新建」） */
export async function apiWithStatus<T>(path: string, init?: RequestInit): Promise<{ status: number; data: T }> {
  if (SNAPSHOT) return (await import('../demo/mock')).demoApi(path, init) as Promise<{ status: number; data: T }>
  const res = await fetch(`/api${path}`, init)
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new ApiError(res.status, errorText(res.status, body))
  }
  return { status: res.status, data: res.status === 204 ? (undefined as T) : await res.json() }
}

/** 调后端接口。开发时 Vite 把 /api 转发给后端；后端没启动时转发会失败，同样当成错误抛出 */
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  return (await apiWithStatus<T>(path, init)).data
}

/** 发 JSON 的请求 */
export function sendJson(method: 'POST' | 'PUT' | 'PATCH', body: unknown): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
}

function errorText(status: number, body: unknown): string {
  if (status === 502 || status === 504) return '连不上后端，请先启动后端服务'
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  // 参数校验失败时 detail 是一组错误，取第一条的说明
  if (Array.isArray(detail) && typeof detail[0]?.msg === 'string') return detail[0].msg.replace(/^Value error, /, '')
  return `请求失败（${status}）`
}
