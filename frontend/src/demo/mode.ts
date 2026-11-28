// 只读快照模式：在线演示版（__DEMO__ 构建）和本地版的「示例数据」共用。
// 打开时所有请求都由 demo/mock.ts 从快照应答，不碰后端和你自己的数据。
// 本地版开关记在 localStorage，切换时整页重新载入，你的数据和示例数据不会混在缓存里。

const KEY = 'qianyue:sample'

/** 本地版正在看示例数据 */
export const SAMPLE = !__DEMO__ && localStorage.getItem(KEY) === 'on'
/** 请求走快照：在线演示或示例数据 */
export const SNAPSHOT = __DEMO__ || SAMPLE

/** to：切过去后打开哪一页，默认总览。只取页面那一段（示例里没有你的岗位编号） */
export function enterSample(to = '/overview') {
  localStorage.setItem(KEY, 'on')
  window.location.assign(to)
}

export function exitSample(to = '/overview') {
  localStorage.setItem(KEY, 'off')
  window.location.assign(to)
}
