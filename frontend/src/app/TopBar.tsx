import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { DEMO_BLOCKED } from '../api/client'
import { exitSample, SNAPSHOT } from '../demo/mode'
import { useHealth } from '../api/health'
import { BrandMark } from '../ui/BrandMark'
import { IconButton, IconLink } from '../ui/IconButton'
import { IconGear, IconHelp, IconMoon, IconSun } from '../ui/icons'
import { SegmentedNav } from '../ui/Segmented'
import { useToast } from '../ui/toast'
import { NAV } from './nav'
import { TasksButton } from './TasksPanel'
import { openGuide } from './guide/guide-store'
import { useTheme } from './theme'
import './top-bar.css'

export function TopBar() {
  const [theme, toggleTheme] = useTheme()
  // 页面滚动后顶栏才加一层模糊，没滚动时背景的光一直铺到顶
  const [scrolled, setScrolled] = useState(false)
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])
  // 品牌动画：打开时播一次，点品牌图或文字再播一次（换 key 让动画重新开始）
  const [brandPlay, setBrandPlay] = useState(0)
  const themeLabel = theme === 'dark' ? '换成浅色' : '换成深色'

  return (
    <header className="top" data-scrolled={scrolled}>
      <button type="button" className="top-brand" onClick={() => setBrandPlay((n) => n + 1)} aria-label="迁跃，点击播放品牌动画">
        <BrandMark key={brandPlay} className="top-mark" tile={theme === 'light'} animated />
        <span className="top-brand-text">迁跃</span>
      </button>
      <div className="top-nav">
        <SegmentedNav items={NAV} label="主导航" size="lg" />
      </div>
      <div className="top-right">
        {SNAPSHOT ? <SnapshotBadge /> : <ConnectionStatus />}
        <TasksButton />
        <IconButton label="使用引导" onClick={openGuide} className="icon-btn glass top-help"><IconHelp /></IconButton>
        <IconButton label={themeLabel} onClick={toggleTheme}>{theme === 'dark' ? <IconSun /> : <IconMoon />}</IconButton>
        <IconLink label="设置" to="/settings"><IconGear /></IconLink>
      </div>
    </header>
  )
}

/** 只读快照：显示「正在看示例 · 退出」。本地版点了退出示例，在线演示版点了回品牌页。
    快照里的导出链接指向不存在的后端，统一拦下来说明 */
function SnapshotBadge() {
  const toast = useToast()
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (!(e.target as Element).closest?.('a[href^="/api/"]')) return
      e.preventDefault()
      toast(DEMO_BLOCKED)
    }
    document.addEventListener('click', onClick, true)
    return () => document.removeEventListener('click', onClick, true)
  }, [toast])
  if (!__DEMO__) {
    return (
      <button type="button" className="top-status glass" data-tone="sample" onClick={() => exitSample()} title="这里是虚构的示例数据，只能浏览。点这里退出示例，回到你自己的数据" aria-label="正在看示例数据，点击退出">
        <i /><span className="top-status-text">正在看示例 · 退出</span>
      </button>
    )
  }
  // 在线演示版：点了回品牌页顶部
  return (
    <a href="/" className="top-status glass" data-tone="sample" title="这里是虚构的示例数据，只能浏览。点这里退出，回到迁跃的介绍页" aria-label="正在看示例数据，点击退出">
      <i /><span className="top-status-text">正在看示例 · 退出</span>
    </a>
  )
}

/** 后端和模型的状态，点了去设置。连接断开或恢复时再弹一句轻提示 */
function ConnectionStatus() {
  const health = useHealth()
  const toast = useToast()
  const wasDown = useRef(false)
  const down = health.isError

  useEffect(() => {
    if (down && !wasDown.current) toast('连不上后端，请先启动后端服务；启动后这里会自动恢复', 'error')
    if (!down && wasDown.current && health.isSuccess) toast('已重新连上后端')
    wasDown.current = down
  }, [down, health.isSuccess, toast])

  const [tone, text] = health.isPending
    ? ['wait', '正在连接后端']
    : down
      ? ['down', '后端未连接']
      : health.data.llm_configured
        ? ['ok', '模型已配置']
        : ['warn', '还没配置模型']
  return (
    <Link to="/settings" className="top-status glass" data-tone={tone} title={`${text}，点这里前往设置`} aria-label={text}>
      <i /><span className="top-status-text">{text}</span>
    </Link>
  )
}
