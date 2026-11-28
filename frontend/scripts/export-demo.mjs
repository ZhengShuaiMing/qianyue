// 把演示数据导出成在线演示版用的快照 src/demo/snapshot.json。
// 先用演示数据启动后端，再运行本脚本：
//   QIANYUE_DATA_DIR=demo-data QIANYUE_PORT=8010 ./scripts/start/start.sh   # 另开一个终端
//   node frontend/scripts/export-demo.mjs
// 快照里的键就是前端请求的路径（不带 /api），演示版按路径原样返回；岗位和成果列表另存一份，由前端按筛选条件分页。
// 注意：每次检索都会被后端记进 demo-data 的搜索历史（快照里存的是检索前读出的历史）。导完想还原 demo-data：
//   sqlite3 demo-data/qianyue.db "attach '../qianyue-old/demo-data/qianyue.db' as o; delete from search_history; insert into search_history select * from o.search_history;"
import { writeFileSync } from 'node:fs'

const BASE = (process.env.QIANYUE_URL ?? 'http://127.0.0.1:8010') + '/api'
// 检索不能在浏览器里跑，只预先搜好这几个词；演示版的检索页会把它们列出来
const SEARCHES = ['Kafka 消费延迟', 'Redis 缓存一致性', '加班', '期望薪资', 'gRPC']
const SEARCH_TYPES = [null, 'material', 'job', 'communication', 'artifact', 'chat']
const JOB_CATEGORIES = [null, 'not_started', 'in_progress', 'closed']

const responses = {}
async function get(path) {
  const res = await fetch(BASE + path)
  if (!res.ok) throw new Error(`${path} → ${res.status}`)
  const data = await res.json()
  responses[path] = data
  return data
}

for (const path of ['/health', '/settings', '/overview', '/jobs/overview', '/collection/platforms', '/collection/browser/status', '/search/history', '/runs?limit=50']) await get(path)

// 岗位：每种「类别 × 排序」只存编号顺序，详情和列表项另存
const jobLists = {}
const jobItems = {}
for (const category of JOB_CATEGORIES) {
  for (const sort of ['recent', 'score']) {
    const params = new URLSearchParams({ limit: '200', offset: '0' })
    if (category) params.set('category', category)
    if (sort !== 'recent') params.set('sort', sort)
    const res = await fetch(`${BASE}/jobs?${params}`).then((r) => r.json())
    jobLists[`${category ?? ''}|${sort}`] = res.items.map((j) => j.id)
    for (const j of res.items) jobItems[j.id] = j
  }
}
for (const id of Object.keys(jobItems)) {
  await get(`/jobs/${id}`)
  await get(`/jobs/${id}/communications`)
}

// 成果：列表按最近更新排好，前端按类型、岗位筛
const artifacts = (await fetch(`${BASE}/artifacts?limit=200&offset=0`).then((r) => r.json())).items
for (const a of artifacts) await get(`/artifacts/${a.id}`)

for (const run of responses['/runs?limit=50'].items) await get(`/runs/${run.id}`)
for (const c of await get('/chat')) await get(`/chat/${c.id}`)

for (const kind of ['resume', 'star', 'supplement']) {
  const versions = await get(`/documents/${kind}/versions`)
  if (versions.length) await get(`/documents/${kind}`)
  for (const v of versions) await get(`/documents/${kind}/versions/${v.id}`)
}

const searches = [...new Set([...SEARCHES, ...responses['/search/history'].map((h) => h.query)])]
for (const q of searches) {
  for (const type of SEARCH_TYPES) {
    const params = new URLSearchParams({ q, limit: '20' })
    if (type) params.set('type', type)
    await get(`/search?${params}`)
  }
}

// 演示版里 AI 显示为已配置，按钮都能点，点了再说明演示版不能运行
responses['/health'] = { ...responses['/health'], llm_configured: true }
responses['/settings'] = { ...responses['/settings'], llm_api_key_set: true, llm_api_key_tail: 'demo' }

const out = new URL('../src/demo/snapshot.json', import.meta.url)
writeFileSync(out, JSON.stringify({ searches, jobLists, jobItems, artifacts, responses }))
console.log(`已导出 ${Object.keys(responses).length} 个接口，${Object.keys(jobItems).length} 个岗位，${artifacts.length} 份成果 → ${out.pathname}`)
