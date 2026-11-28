import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, sendJson } from './client'

export type SearchProvider = 'ddgs' | 'bocha' | 'zhipu' | 'tavily' | 'brave' | 'serper' | 'exa'
export type SecretName = 'llm_api_key' | 'search_api_key'

export type AppSettings = {
  llm_base_url: string
  llm_model: string
  hide_personal_info: boolean
  privacy_terms: string
  /** 有没有 Key。Key 本身不在这里返回，按住眼睛时用 readSecret 取 */
  llm_api_key_set: boolean
  llm_api_key_tail: string
  search_provider: SearchProvider
  search_api_key_set: boolean
  search_api_key_tail: string
  /** 是不是在设置页保存过；没保存过时显示的是 backend/.env 里的值 */
  saved_in_app: boolean
}

type SettingsSave = Pick<AppSettings, 'llm_base_url' | 'llm_model'> & {
  /** 不传表示沿用现在的 Key */
  llm_api_key?: string
} | {
  search_provider: SearchProvider
  search_api_key?: string
} | Pick<AppSettings, 'hide_personal_info' | 'privacy_terms'>

const settingsKey = ['settings'] as const

export function readSettings(signal?: AbortSignal) {
  return api<AppSettings>('/settings', { signal, cache: 'no-store' })
}

export function useSettings() {
  return useQuery({ queryKey: settingsKey, queryFn: ({ signal }) => readSettings(signal) })
}

/** 保存设置，立即生效 */
export function useSaveSettings() {
  const client = useQueryClient()
  return useMutation({
    scope: { id: 'settings' },
    mutationFn: async (body: SettingsSave) => {
      if (!('llm_base_url' in body)) {
        // 搜索和隐私取已保存的模型配置，满足接口必填项，不带入模型卡的草稿。
        const { llm_base_url, llm_model } = client.getQueryData<AppSettings>(settingsKey) ?? await readSettings()
        return api<AppSettings>('/settings', sendJson('PUT', { llm_base_url, llm_model, ...body }))
      }
      return api<AppSettings>('/settings', sendJson('PUT', body))
    },
    onSuccess: async (saved) => {
      await client.cancelQueries({ queryKey: settingsKey })
      client.setQueryData(settingsKey, saved)
      return client.invalidateQueries({ queryKey: ['health'] })
    },
  })
}

/** 取一次完整的 Key（按住眼睛时）。不进缓存，松开就丢 */
export async function readSecret(name: SecretName): Promise<string> {
  return (await api<{ value: string }>(`/settings/secret/${name}`)).value
}

/** 测试当前表单里填的值，不用先保存。不传的项用已保存的配置 */
export function probeSettings(service: 'model' | 'search', body: unknown) {
  return api<{ status: string; message: string }>(`/settings/test/${service}`, sendJson('POST', body))
}
