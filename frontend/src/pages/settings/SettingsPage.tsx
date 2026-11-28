import { useMutation } from '@tanstack/react-query'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useLocation } from 'react-router'
import { enterSample, exitSample, SAMPLE } from '../../demo/mode'
import { openGuide } from '../../app/guide/guide-store'
import { probeSettings, readSecret, useSaveSettings, useSettings, type AppSettings, type SearchProvider, type SecretName } from '../../api/settings'
import { Button } from '../../ui/Button'
import { EmptyState } from '../../ui/EmptyState'
import { Field, TextArea, TextInput } from '../../ui/Field'
import { Group } from '../../ui/Group'
import { IconCopy, IconEye } from '../../ui/icons'
import { useToast } from '../../ui/toast'
import './settings.css'

// 和后端 app/tools/web.py 的 PROVIDERS 一一对应。site 是申请 Key 的网站；DuckDuckGo 不用 Key
const PROVIDERS: { key: SearchProvider; label: string; site?: string }[] = [
  { key: 'ddgs', label: 'DuckDuckGo（免费）' },
  { key: 'bocha', label: '博查', site: 'open.bochaai.com' },
  { key: 'zhipu', label: '智谱', site: 'bigmodel.cn' },
  { key: 'tavily', label: 'Tavily', site: 'tavily.com' },
  { key: 'brave', label: 'Brave', site: 'brave.com/search/api' },
  { key: 'serper', label: 'Serper', site: 'serper.dev' },
  { key: 'exa', label: 'Exa', site: 'exa.ai' },
]
/** 中文句子里夹英文名时两边空一格，中文名不空 */
const inline = (name: string) => (/^[ -~]+$/.test(name) ? ` ${name} ` : name)
const MASK = '••••••••••••••••'

export function SettingsPage() {
  const current = useSettings()
  const settings = current.isError ? undefined : current.data
  return (
    <div className="settings">
      <h1 className="page-title">设置</h1>
      {current.isError ? (
        <EmptyState title="设置没有读出来" action={<Button onClick={() => current.refetch()}>再试一次</Button>}>{current.error.message}</EmptyState>
      ) : !settings && (
        <p className="settings-note">正在读取…</p>
      )}
      <div className="settings-cols">
        {settings && <ModelCard key={JSON.stringify([settings.llm_base_url, settings.llm_model, settings.llm_api_key_tail, settings.llm_api_key_set])} current={settings} />}
        {settings && <SearchCard key={`${settings.search_provider}|${settings.search_api_key_tail}|${settings.search_api_key_set}`} current={settings} />}
        <Mcp />
        {settings && <PrivacyCard key={JSON.stringify([settings.hide_personal_info, settings.privacy_terms])} current={settings} />}
        {!__DEMO__ && <SampleCard />}
      </div>
    </div>
  )
}

function ModelCard({ current }: { current: AppSettings }) {
  const [baseUrl, setBaseUrl] = useState(current.llm_base_url)
  const [model, setModel] = useState(current.llm_model)
  const [key, setKey] = useState('')
  const save = useSaveSettings()
  const toast = useToast()
  const changed = baseUrl.trim() !== current.llm_base_url || model.trim() !== current.llm_model || key.trim() !== ''
  const submit = () => {
    if (!changed || save.isPending) return
    save.mutate(
      {
        llm_base_url: baseUrl.trim(),
        llm_model: model.trim(),
        ...(key.trim() ? { llm_api_key: key.trim() } : {}),
      },
      { onSuccess: () => { setKey((value) => value === key ? '' : value); toast('模型设置已保存，立即生效') } },
    )
  }
  return (
    <Group label="模型" hint="支持任意提供 OpenAI 兼容接口的模型" plain>
      <form className="box settings-box" onSubmit={(e) => { e.preventDefault(); submit() }}>
        <Field label={<span>模型服务地址<small>（Base URL）</small></span>}>
          <TextInput value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://" />
        </Field>
        <Field label="模型名">
          <TextInput value={model} onChange={(e) => setModel(e.target.value)} />
        </Field>
        <Field label="Key">
          <KeyInput name="llm_api_key" saved={current.llm_api_key_set} value={key} onChange={setKey} />
        </Field>
        <ConnectionBar
          service="model"
          probe={{
            llm_base_url: baseUrl.trim(),
            llm_model: model.trim(),
            ...(key.trim() ? { llm_api_key: key.trim() } : {}),
          }}
          probeReady={!!baseUrl.trim() && !!model.trim()}
          keySource={key.trim() ? 'typed' : current.llm_api_key_set ? 'saved' : null}
          note={save.isError ? `没有保存成功：${save.error.message}` : current.saved_in_app ? '截图识别需要模型支持图片输入；模型不支持时，添加截图会失败。' : '现在用的是 backend/.env 里的配置，在这里保存后以这里为准'}
          error={save.isError}
          disabled={!changed || save.isPending}
          busy={save.isPending}
        />
      </form>
    </Group>
  )
}

/** 示例数据和新手引导：随时可以再看一遍。示例只读，进出都会整页重新载入 */
function SampleCard() {
  return (
    <Group label="示例数据与引导" plain>
      <div className="box settings-box settings-sample">
        <p className="settings-mcp-note">
          {SAMPLE
            ? '正在看示例数据：一份虚构的求职记录，只能浏览。退出后回到你自己的数据。'
            : '示例数据是一份虚构的求职记录（陈XX 的二十多个岗位、评估、定制简历和面试准备），只能浏览，不会碰你自己的数据。'}
        </p>
        <div className="settings-sample-actions">
          <Button onClick={() => (SAMPLE ? exitSample() : enterSample())}>{SAMPLE ? '退出示例' : '查看示例数据'}</Button>
          <Button onClick={openGuide}>看引导</Button>
        </div>
      </div>
    </Group>
  )
}

function PrivacyCard({ current }: { current: AppSettings }) {
  const [hidePersonalInfo, setHidePersonalInfo] = useState(current.hide_personal_info)
  const [privacyTerms, setPrivacyTerms] = useState(current.privacy_terms)
  const save = useSaveSettings()
  const toast = useToast()
  const changed = hidePersonalInfo !== current.hide_personal_info || privacyTerms !== current.privacy_terms
  const submit = () => {
    if (!changed || save.isPending) return
    save.mutate(
      { hide_personal_info: hidePersonalInfo, privacy_terms: privacyTerms },
      { onSuccess: () => toast('隐私设置已保存，立即生效') },
    )
  }
  return (
    <Group label="隐私" plain>
      <form className="box settings-box settings-privacy" onSubmit={(e) => { e.preventDefault(); submit() }}>
        <div className="settings-privacy-head">
          <label className="settings-privacy-toggle">
            <input type="checkbox" checked={hidePersonalInfo} onChange={(e) => setHidePersonalInfo(e.target.checked)} />
            <span>发送给模型前隐藏个人信息</span>
          </label>
          <p>默认开启，会脱敏发送给模型的文本，如邮箱、手机号。截图没法脱敏，会以原图发给 AI，发送前请自己遮挡个人信息。关闭并保存后文本也按原文发送。</p>
        </div>
        <Field label="自定义脱敏词" hint="每行一个，也可以用空格分开">
          <TextArea size="short" value={privacyTerms} onChange={(e) => setPrivacyTerms(e.target.value)} placeholder="示例：姓名 公司名 项目名" />
        </Field>
        <dl className="settings-privacy-sample">
          <div><dt>原文</dt><dd>邮箱为 candidate@example.com，曾在示例公司工作。</dd></div>
          <div><dt>脱敏后</dt><dd>邮箱为〔邮箱〕，曾在〔已隐藏〕工作。</dd></div>
        </dl>
        <CardBar
          note={save.isError ? `没有保存成功：${save.error.message}` : undefined}
          error={save.isError}
          disabled={!changed || save.isPending}
          busy={save.isPending}
        />
      </form>
    </Group>
  )
}

function SearchCard({ current }: { current: AppSettings }) {
  const [provider, setProvider] = useState<SearchProvider>(current.search_provider)
  const [searchKey, setSearchKey] = useState('')
  const save = useSaveSettings()
  const toast = useToast()
  const chosen = PROVIDERS.find((p) => p.key === provider)!
  const needsKey = provider !== 'ddgs'
  const sameProvider = provider === current.search_provider
  // 要 Key 的搜索服务：换了服务必须填这家的 Key；没换且已经填过可以不填
  const keyMissing = needsKey && !searchKey.trim() && (!sameProvider || !current.search_api_key_set)
  const changed = !sameProvider || searchKey.trim() !== ''
  const submit = () => {
    if (!changed || keyMissing || save.isPending) return
    save.mutate(
      {
        search_provider: provider,
        ...(needsKey && searchKey.trim() ? { search_api_key: searchKey.trim() } : {}),
      },
      { onSuccess: () => toast('联网搜索设置已保存，立即生效') },
    )
  }
  return (
    <Group label="联网搜索" hint="用于公司研究，以及面试准备中查找面经" plain>
      <form className="box settings-box" onSubmit={(e) => { e.preventDefault(); submit() }}>
        <div className="field">
          <span className="field-label" id="search-provider-label">服务商</span>
          <div className="search-options" role="radiogroup" aria-labelledby="search-provider-label">
            {PROVIDERS.map((p) => (
              <label key={p.key} className="search-option" data-checked={provider === p.key}>
                <input type="radio" name="search-provider" value={p.key} checked={provider === p.key} onChange={() => setProvider(p.key)} />
                {p.label}
              </label>
            ))}
          </div>
        </div>
        {/* Key 一直占着位置（DuckDuckGo 时不能填），切换服务商时下面不跳；和左边模型的 Key 对齐 */}
        <Field label="Key">
          <KeyInput
            key={provider}
            name="search_api_key"
            saved={needsKey && sameProvider && current.search_api_key_set}
            value={needsKey ? searchKey : ''}
            onChange={setSearchKey}
            placeholder={needsKey ? `填${inline(chosen.label)}的 Key` : 'DuckDuckGo 不用 Key'}
            disabled={!needsKey}
          />
        </Field>
        <ConnectionBar
          service="search"
          probe={{
            search_provider: provider,
            ...(needsKey && searchKey.trim() ? { search_api_key: searchKey.trim() } : {}),
          }}
          probeReady
          keySource={needsKey ? (searchKey.trim() ? 'typed' : sameProvider && current.search_api_key_set ? 'saved' : null) : null}
          note={
            save.isError ? `没有保存成功：${save.error.message}`
              : needsKey ? `${inline(chosen.label).trimStart()}要用你自己的 Key，在 ${chosen.site} 申请`
                : 'DuckDuckGo 免费，不用 Key'
          }
          error={save.isError}
          disabled={!changed || keyMissing || save.isPending}
          busy={save.isPending}
        />
      </form>
    </Group>
  )
}

function ConnectionBar({ service, probe, probeReady, keySource, ...save }: CardBarProps & {
  service: 'model' | 'search'
  /** 当前表单里填的值，测试直接用它，不用先保存 */
  probe: Record<string, unknown>
  probeReady: boolean
  /** 这次测试用的是哪把 Key：刚填的、已保存的，或者不需要 Key */
  keySource: 'typed' | 'saved' | null
}) {
  const test = useMutation({ mutationFn: () => probeSettings(service, probe) })
  const model = service === 'model'
  // 改了地址、Key 或服务商，上一次的结果就不再代表现在填的东西，直接清掉
  const fingerprint = JSON.stringify(probe)
  useEffect(() => { test.reset() }, [fingerprint]) // eslint-disable-line react-hooks/exhaustive-deps
  const ok = !test.isError && test.data?.status === 'ok'
  const done = test.isError || !!test.data
  // 详细原因放在悬停提示里，界面上只说成功或失败
  const detail = test.error?.message || test.data?.message
  // 说清楚测的是哪把 Key，不然用户不知道自己测了什么
  const which = keySource === 'typed' ? '（用刚填的 Key）' : keySource === 'saved' ? '（用已保存的 Key）' : ''
  return (
    <CardBar
      {...save}
      // 这一行始终占着位置：出现和消失都不改变卡片高度，也不挤动上面的输入框
      probeNote={
        <span className="settings-probe-result" role="status" data-tone={done && !ok ? 'error' : undefined} title={done && !ok ? detail : undefined}>
          {done ? `${ok ? '连接成功' : '连接失败'}${which}` : ''}
        </span>
      }
      probeButton={
        <Button size="sm" disabled={!probeReady || test.isPending} onClick={() => test.mutate()}>
          {test.isPending ? '测试中…' : model ? '测试模型' : '测试搜索'}
        </Button>
      }
    />
  )
}

type CardBarProps = { note?: string; error: boolean; disabled: boolean; busy: boolean }

/** 卡片底部：左边说明和反馈，右边先测试再保存（如有测试操作） */
function CardBar({ note, error, disabled, busy, probeNote, probeButton }: CardBarProps & { probeNote?: ReactNode; probeButton?: ReactNode }) {
  return (
    <div className="settings-bar">
      {(note || probeNote) && <div className="settings-feedback">
        {note && <span data-tone={error ? 'error' : undefined}>{note}</span>}
        {probeNote}
      </div>}
      <div className="settings-actions">
        {probeButton}
        <Button type="submit" size="sm" variant="primary" disabled={disabled}>{busy ? '正在保存…' : '保存'}</Button>
      </div>
    </div>
  )
}

/** Key 输入框：已经存过时用圆点表示；按住右边的眼睛显示（存过的就向后端取一次），松开隐藏 */
function KeyInput({ name, saved, value, onChange, placeholder, disabled }: {
  name: SecretName
  saved: boolean
  value: string
  onChange: (v: string) => void
  placeholder?: string
  disabled?: boolean
}) {
  const [shown, setShown] = useState<string | null>(null)
  const hold = useRef<symbol | null>(null)
  const toast = useToast()
  const show = async () => {
    if (hold.current || (!value && !saved)) return
    const press = Symbol()
    hold.current = press
    try {
      const secret = value || await readSecret(name)
      // 只允许仍在按住的这次操作显示 Key，忽略松开后才返回的结果。
      if (hold.current === press) setShown(secret)
    } catch (e) {
      if (hold.current === press) toast(`Key 没有读出来：${(e as Error).message}`, 'error')
    }
  }
  const hide = () => {
    hold.current = null
    setShown(null)
  }
  return (
    <span className="key-input">
      <TextInput
        type={shown === null ? 'password' : 'text'}
        value={shown ?? value}
        readOnly={shown !== null}
        onChange={(e) => onChange(e.target.value)}
        placeholder={saved ? MASK : placeholder ?? 'sk-…'}
        disabled={disabled}
        autoComplete="off"
        spellCheck={false}
      />
      <button
        type="button"
        className="key-eye"
        aria-label="按住显示 Key"
        title="按住显示 Key"
        disabled={!saved && !value}
        onPointerDown={(e) => { e.preventDefault(); void show() }}
        onPointerUp={hide}
        onPointerCancel={hide}
        onPointerLeave={hide}
        onKeyDown={(e) => { if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); void show() } }}
        onKeyUp={hide}
        onBlur={hide}
      >
        <IconEye />
      </button>
    </span>
  )
}

/** 接入你自己的 Agent：MCP 地址和可以复制的配置，没有要改的项 */
function Mcp() {
  const toast = useToast()
  const url = `${window.location.origin}/mcp`
  const config = JSON.stringify({ mcpServers: { qianyue: { url } } }, null, 2)
  // 会自己改配置的 Agent（Claude Code、Codex 等）直接把这段话发给它
  const instruction = `请把迁跃加到你的 MCP 服务里：名称 qianyue，地址 ${url}，传输方式 HTTP（Streamable HTTP）。`
    + '加好后把迁跃的工具设为始终允许，再列出迁跃提供的工具，确认已经连上。'
  const copy = (text: string, what: string) => {
    navigator.clipboard.writeText(text).then(() => toast(`已复制${what}`), () => toast('没有复制成功，请手动选中复制', 'error'))
  }
  // 从「交给你的 Agent」弹窗点过来时，直接滚到这一组
  const { hash } = useLocation()
  // 等外壳的滚动还原（换页回到顶部）做完再滚，不然会被它盖掉
  useEffect(() => {
    if (hash !== '#mcp') return
    const frame = requestAnimationFrame(() => document.getElementById('mcp')?.scrollIntoView({ block: 'start' }))
    return () => cancelAnimationFrame(frame)
  }, [hash])
  return (
    <Group id="mcp" label="接入你自己的 Agent（MCP）" plain>
      <div className="box settings-box settings-mcp">
        <div className="settings-mcp-row">
          <span>地址</span>
          <span className="settings-mcp-url">
            <code>{url}</code>
            <button type="button" className="copy-icon" aria-label="复制地址" title="复制地址" onClick={() => copy(url, '地址')}><IconCopy /></button>
          </span>
        </div>
        <div className="settings-mcp-row settings-mcp-config">
          <span>配置</span>
          <div className="settings-mcp-pre">
            <pre>{config}</pre>
            <button type="button" className="copy-icon" aria-label="复制配置" title="复制配置" onClick={() => copy(config, '配置')}><IconCopy /></button>
          </div>
        </div>
        <p className="settings-mcp-note">将以上配置写入你要连接的 Agent（Codex、Claude Code、Cursor 等）的 MCP 设置里，或者将以下指令发送给 Agent，让它自己配置：</p>
        <div className="settings-mcp-row settings-mcp-config">
          <span>指令</span>
          <div className="settings-mcp-pre">
            <pre className="settings-mcp-say">{instruction}</pre>
            <button type="button" className="copy-icon" aria-label="复制指令" title="复制指令" onClick={() => copy(instruction, '指令')}><IconCopy /></button>
          </div>
        </div>
        <p className="settings-mcp-note">连上之后，没有配置模型 Key 也能用评估、定制简历等 AI 功能，由你的 Agent 来完成。试试对它说「用迁跃评估岗位 #12」。</p>
      </div>
    </Group>
  )
}
