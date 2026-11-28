import { IconClose, IconSearch } from './icons'
import './glass.css'
import './search-field.css'

/** 胶囊搜索框：点进去只出现光标，不加外框。有字时右边出现清空按钮 */
export function SearchField({ value, onChange, placeholder, label }: {
  value: string
  onChange: (value: string) => void
  placeholder: string
  label: string
}) {
  return (
    <label className="search glass">
      <IconSearch />
      <input type="search" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} aria-label={label} />
      {value && (
        <button type="button" className="search-clear" onClick={() => onChange('')} aria-label="清空搜索" title="清空搜索"><IconClose /></button>
      )}
    </label>
  )
}
