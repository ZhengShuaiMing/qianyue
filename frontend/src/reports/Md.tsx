import { useState, type ComponentProps, type ReactNode } from 'react'
import Markdown from 'react-markdown'
import { Link } from 'react-router'
import remarkGfm from 'remark-gfm'
import { Button } from '../ui/Button'
import './md.css'

/** 成果正文是 Markdown。代码块在这些成果里其实是可以直接发出去的草稿，换成带复制按钮的卡片 */
export function Md({ children }: { children: string }) {
  return <Markdown remarkPlugins={[remarkGfm]} components={{ pre: Draft, a: Anchor }}>{children}</Markdown>
}

/** 迁跃自己的地址（/ 开头，例如助手给的「在检索页看全部结果」）在页面里跳转；外面的网址开新标签页 */
function Anchor({ href = '', children }: ComponentProps<'a'>) {
  if (href.startsWith('/')) return <Link to={href}>{children}</Link>
  return <a href={href} target="_blank" rel="noreferrer">{children}</a>
}

function Draft({ children }: { children?: ReactNode }) {
  const [copied, setCopied] = useState(false)
  const text = textOf(children).replace(/\n$/, '')
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch { /* 浏览器不让写剪贴板时，用户还能手动选中复制 */ }
  }
  return (
    <div className="draft">
      <p>{text}</p>
      <Button size="sm" onClick={copy}>{copied ? '已复制' : '复制'}</Button>
    </div>
  )
}

function textOf(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(textOf).join('')
  if (node && typeof node === 'object' && 'props' in node) return textOf((node as { props: { children?: ReactNode } }).props.children)
  return ''
}
