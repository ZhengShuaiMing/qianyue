// 给 README 截图：用 demo-data（示例求职数据，人名是「陈XX」）启动迁跃，只生成深色 WebP，
// 输出到 media/readme/screenshots/。
//
//   QIANYUE_DATA_DIR=demo-data QIANYUE_PORT=8010 QIANYUE_LLM_API_KEY=placeholder ./scripts/start/start.sh   # 另开一个终端
//   NODE_PATH=<装了 playwright 的 node_modules> node frontend/scripts/readme-shots.cjs [名字…]
//
// Key 填占位值只是让顶栏显示「模型已配置」，截图时不调用模型。检索页的截图会在 demo-data 里记一条搜索历史。
// 需要 playwright、系统里的 Chrome，以及 cwebp、img2webp（brew install webp）。
// 截图的内容依赖 demo-data 里的编号，重新生成数据后要核对地址。
//
// 两种产出：
// 1. 静态图：一屏大小（总览高一些，要把节奏图放进来）。成果、检索底部渐隐。
// 2. 岗位详情、材料的全页动图：依次切换页签并向下滚动。

const { chromium } = require('playwright')
const { execFileSync } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const BASE = process.env.QIANYUE_URL ?? 'http://127.0.0.1:8010'
const OUT = path.resolve(__dirname, '../../media/readme/screenshots')
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'qianyue-shots-'))
const WIDTH = 1440

// ---------- 静态图 ----------

const STATIC = [
  { name: 'overview', url: '/overview', fullHeight: true, before: (page) => page.getByRole('tab', { name: '按周' }).click() },
  { name: 'collect', url: '/collect', fullHeight: true }, // 整页，把底部的「开始采集」也截进来
  { name: 'artifacts', url: '/artifacts/34', fade: true },
  { name: 'search', url: '/search?q=' + encodeURIComponent('Kafka 消费延迟'), fade: true },
  { name: 'assistant', url: '/assistant/f4ad6b28aab240369babf59b00ec9819' },
]

// ---------- 页签轮播 ----------

const JOB_TABS = ['info', 'evaluation', 'tailoring', 'research', 'communication', 'interview_prep', 'mock']
const CAROUSELS = [
  {
    name: 'job-tabs',
    tabs: JOB_TABS.map((key) => ({ key, url: `/jobs/1${key === 'info' ? '' : `?tab=${key}`}` })),
  },
  {
    name: 'materials-tabs',
    tabs: ['resume', 'star', 'supplement'].map((key) => ({ key, url: `/materials${key === 'resume' ? '' : `?kind=${key}`}` })),
  },
]
const BODY_MAX = 3200 // 正文最多截多高（CSS 像素），再长的不往下截
const SCROLL_MAX = 900 // 动图里每个页签最多往下滚多少

async function newPage(browser, scheme, scale) {
  const context = await browser.newContext({
    viewport: { width: WIDTH, height: 900 },
    deviceScaleFactor: scale,
    colorScheme: scheme,
    locale: 'zh-CN',
  })
  await context.addInitScript(() => (localStorage.setItem('qianyue:guide', '{"all":99}'), localStorage.setItem('qianyue:guide-intro', 'shown'))) // 不自动播放导览
  return { context, page: await context.newPage() }
}

async function open(page, url, height = 900) {
  await page.setViewportSize({ width: WIDTH, height })
  await page.goto(BASE + url, { waitUntil: 'networkidle' })
  await page.evaluate(() => document.fonts.ready)
  await page.mouse.move(0, 0) // 鼠标移开，免得停在某个元素上显示悬停样式
}

// 背景的三团云一直在飘，截图前定在同一个时刻，让每次重截都一样
async function freezeClouds(page) {
  await page.evaluate(() => {
    for (const animation of document.getAnimations()) {
      if (animation.effect?.target?.closest?.('.atmo')) {
        animation.pause()
        animation.currentTime = 30000
      }
    }
  })
}

// 轮播的素材要拼接，背景换成纯色、去掉颗粒，拼缝看不出来，压缩也小
// 长截图的背景：窗口拉到整页那么高时，光团的大小和位置按窗口高度（vh）算，会被拉成长条；以前干脆换成纯色底，截出来比产品黑。
// 现在保留光团：动画停住、颜色固定，大小按 1440×900 窗口时的像素算，沿整张图往下左右交替每隔约 760px 放一团。
// 一次截完整张图，不用拼接，所以没有接缝。要在把窗口拉高之后调用
async function tallAtmosphere(page) {
  await page.evaluate(() => {
    const atmo = document.querySelector('.atmo')
    const proto = atmo?.querySelector('.cloud')
    if (!atmo || !proto) return
    const H = document.documentElement.scrollHeight
    const W = innerWidth
    const colors = ['#86a8cf', '#a49ed6', '#d39aa6', '#7fb3b5', '#84b598', '#d2a473']
    for (const c of atmo.querySelectorAll('.cloud')) c.remove()
    const n = Math.max(3, Math.ceil(H / 760))
    for (let i = 0; i < n; i++) {
      const c = proto.cloneNode(true)
      const x = (i % 2 ? 0.74 : 0.26) * W
      const y = ((i + 0.5) / n) * H
      c.style.cssText = `animation: none; left: ${x}px; top: ${y}px; width: 1150px; height: 720px; min-width: 0; min-height: 0; transform: translate(-50%, -50%)`
      const glow = c.firstElementChild
      glow.style.animation = 'none'
      glow.style.setProperty('--cyc', colors[i % colors.length])
      atmo.append(c)
    }
  })
}

function webp(png, out, quality = 86) {
  fs.mkdirSync(path.dirname(out), { recursive: true })
  execFileSync('cwebp', ['-quiet', '-q', String(quality), png, '-o', out])
}

async function shootStatic(page, scheme, shot) {
  await open(page, shot.url)
  if (shot.before) await shot.before(page)
  if (shot.fullHeight) {
    const height = await page.evaluate(() => document.documentElement.scrollHeight)
    await page.setViewportSize({ width: WIDTH, height })
    await tallAtmosphere(page)
  }
  await freezeClouds(page)
  if (shot.fade) {
    // 底部模糊渐入：越往下越糊、越接近底色
    await page.addStyleTag({
      content: `.shot-fade { position: fixed; inset: auto 0 0; height: 220px; z-index: 40; pointer-events: none;
        backdrop-filter: blur(6px); -webkit-mask-image: linear-gradient(transparent, #000 75%); mask-image: linear-gradient(transparent, #000 75%);
        background: linear-gradient(transparent, var(--ground) 92%); }`,
    })
    await page.evaluate(() => document.body.append(Object.assign(document.createElement('div'), { className: 'shot-fade' })))
  }
  await page.waitForTimeout(900) // 等进场动画和图表画完
  const png = path.join(TMP, `${scheme}-${shot.name}.png`)
  await page.screenshot({ path: png })
  webp(png, path.join(OUT, `${shot.name}.webp`))
  console.log(`${shot.name}.webp`)
}

// 每个页签保留一张全页临时图，用于合成 README 动图。
async function shootTab(page, scheme, carousel, tab) {
  await open(page, tab.url)
  const full = await page.evaluate(() => document.documentElement.scrollHeight)
  await page.setViewportSize({ width: WIDTH, height: Math.min(full, BODY_MAX + 600) })
  await tallAtmosphere(page)
  await page.waitForTimeout(700)
  const fullBox = await page.evaluate(() => {
    const outer = document.querySelector('.app').getBoundingClientRect()
    return { x: Math.max(outer.left, 0), y: Math.max(outer.top, 0), width: outer.width, bottom: outer.bottom }
  })
  const png = path.join(TMP, `${scheme}-${carousel.name}-${tab.key}-full.png`)
  const height = Math.min(fullBox.bottom - fullBox.y, BODY_MAX + 600)
  await page.screenshot({ path: png, clip: { x: fullBox.x, y: fullBox.y, width: fullBox.width, height } })
  return { png, height, width: fullBox.width }
}

// 动图：把每个页签的全页图放进一个小页面，正文在固定高度的窗口里往下滚，一帧一帧截下来
async function animate(browser, scheme, carousel, shots) {
  const { context, page } = await newPage(browser, scheme, 1)
  const width = shots[0].width
  const winH = Math.round(width * 900 / WIDTH)
  await page.setViewportSize({ width: Math.ceil(width), height: winH })
  await page.goto(BASE + '/overview', { waitUntil: 'networkidle' }) // 借用页面的底色变量
  const ground = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--ground').trim())
  await page.setContent(`<style>
    html, body { margin: 0; background: ${ground}; overflow: hidden; }
    .win { position: relative; height: ${winH}px; overflow: hidden; }
    .win img { position: absolute; left: 0; top: 0; width: ${width}px; }
    .win::after { content: ''; position: absolute; inset: auto 0 0; height: 120px; background: linear-gradient(transparent, ${ground}); }
  </style><div class="win"><img class="body"></div>`)

  const frames = []
  let n = 0
  const frame = async (ms) => {
    const png = path.join(TMP, `${scheme}-${carousel.name}-f${String(n++).padStart(4, '0')}.png`)
    await page.screenshot({ path: png })
    frames.push('-d', String(ms), png)
  }
  for (const shot of shots) {
    await page.evaluate(async (src) => {
      const img = document.querySelector('.body')
      img.src = src
      await img.decode()
      img.style.top = '0px'
    }, 'data:image/png;base64,' + fs.readFileSync(shot.png, 'base64'))
    const distance = Math.min(Math.max(shot.height - winH, 0), SCROLL_MAX)
    await frame(distance ? 1600 : 3000)
    if (!distance) continue
    const steps = Math.round(distance / 50)
    for (let i = 1; i <= steps; i++) {
      const t = i / steps
      const eased = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2
      await page.evaluate((top) => (document.querySelector('.body').style.top = `${-top}px`), Math.round(eased * distance))
      await frame(i === steps ? 1400 : 70)
    }
  }
  await context.close()
  const out = path.join(OUT, `${carousel.name}.webp`)
  // 每帧都是独立关键帧，避免增量编码在整页切换时留下上一页的叠影。
  execFileSync('img2webp', ['-loop', '0', '-kmin', '1', '-kmax', '1', '-lossy', '-q', '62', '-m', '6', ...frames, '-o', out])
  console.log(`${carousel.name}.webp  ${frames.length / 3} 帧`)
}

async function main() {
  const only = process.argv.slice(2)
  const wanted = (name) => !only.length || only.includes(name)
  const browser = await chromium.launch({ channel: 'chrome' }) // 用系统里的 Chrome，不另下浏览器
  const scheme = 'dark'
  const { context, page } = await newPage(browser, scheme, 2)
  for (const shot of STATIC) if (wanted(shot.name)) await shootStatic(page, scheme, shot)
  for (const carousel of CAROUSELS) {
    if (!wanted(carousel.name)) continue
    const shots = []
    for (const tab of carousel.tabs) shots.push(await shootTab(page, scheme, carousel, tab))
    await animate(browser, scheme, carousel, shots)
  }
  await context.close()
  await browser.close()
  fs.rmSync(TMP, { recursive: true, force: true })
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
