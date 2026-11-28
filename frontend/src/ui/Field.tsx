import type { ComponentProps, ReactNode } from 'react'
import './field.css'

/** 表单里的一项：上面是名称（可带一句说明），下面是输入框。
    说明里有按钮时传 htmlFor：只让名称指向输入框，免得点名称时点到说明里的按钮 */
export function Field({ label, hint, htmlFor, children }: { label: ReactNode; hint?: ReactNode; htmlFor?: string; children: ReactNode }) {
  // 纯文字的说明放不下时省略，悬停看全文；带按钮的说明（例如「选图片」）照常换行，免得按钮被藏起来
  const note = hint && (typeof hint === 'string' ? <small className="clip" title={hint}>{hint}</small> : <small>{hint}</small>)
  if (htmlFor) {
    return (
      <div className="field">
        <span className="field-label"><label htmlFor={htmlFor}>{label}</label>{note}</span>
        {children}
      </div>
    )
  }
  return (
    <label className="field">
      <span className="field-label">{label}{note}</span>
      {children}
    </label>
  )
}

export function TextInput(props: ComponentProps<'input'>) {
  return <input className="input" {...props} />
}

/** 多行输入。默认够贴一整份 JD；short 用于备注这类一两句话 */
export function TextArea({ size, ...props }: ComponentProps<'textarea'> & { size?: 'short' }) {
  return <textarea className="input input-area" data-size={size} {...props} />
}
