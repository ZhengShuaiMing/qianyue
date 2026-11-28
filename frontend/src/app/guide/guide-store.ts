import { SNAPSHOT } from '../../demo/mode'

/** 打开引导的入口弹窗：顶栏问号、设置页发这个事件 */
export const OPEN_GUIDE_EVENT = 'qianyue:guide-open'
export const openGuide = () => window.dispatchEvent(new Event(OPEN_GUIDE_EVENT))

/** 第一次打开迁跃时自动弹一次入口弹窗，弹过就记下（截图脚本也用它关掉） */
const INTRO_KEY = 'qianyue:guide-intro'
export const introShown = () => localStorage.getItem(INTRO_KEY) !== null
export const markIntroShown = () => { try { localStorage.setItem(INTRO_KEY, 'shown') } catch { /* 存不了下次再弹 */ } }

/** page 只走当前页，all 按导航栏走完全部页面 */
export type GuideMode = 'page' | 'all'
/** 一次引导：从哪一页开始；exitSample 表示是为了引导才切到示例数据的，结束后切回你的数据 */
export type GuideRun = { mode: GuideMode; path: string; exitSample: boolean }

/** 引导要用示例数据。还在你自己的数据里时，先把这次引导记下，切到示例（整页重新载入）再开始 */
const RUN_KEY = 'qianyue:guide-run'

export function takePendingRun(): GuideRun | null {
  try {
    const raw = sessionStorage.getItem(RUN_KEY)
    sessionStorage.removeItem(RUN_KEY)
    return raw ? (JSON.parse(raw) as GuideRun) : null
  } catch {
    return null
  }
}

/** 开始引导。返回 null 表示要先切到示例数据，页面马上重新载入 */
export function beginRun(mode: GuideMode, path: string, switchToSample: (to: string) => void): GuideRun | null {
  if (SNAPSHOT) return { mode, path, exitSample: false }
  sessionStorage.setItem(RUN_KEY, JSON.stringify({ mode, path, exitSample: true } satisfies GuideRun))
  switchToSample(path)
  return null
}

/** 每一页第一次打开时自动播放本页引导，看过（或在全部引导里走过）就记下：{ 页面路径: 版本 }。
    all 表示全部当作看过到这个版本（截图脚本用它关掉自动播放）。改了某页的文案就把 GUIDE_VERSION 加一 */
const SEEN_KEY = 'qianyue:guide'
export const GUIDE_VERSION = 2

function seenMap(): Record<string, number> {
  try { return JSON.parse(localStorage.getItem(SEEN_KEY) ?? '{}') as Record<string, number> } catch { return {} }
}
export const needsAutoplay = (path: string) => {
  const seen = seenMap()
  return (seen[path] ?? seen.all ?? 0) < GUIDE_VERSION
}
export function markSeen(path: string) {
  try { localStorage.setItem(SEEN_KEY, JSON.stringify({ ...seenMap(), [path]: GUIDE_VERSION })) } catch { /* 下次再播一次 */ }
}
