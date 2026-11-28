import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, apiWithStatus, sendJson } from './client'

/** resume 基础简历（评估必需）、supplement 补充资料、star STAR 经历 */
export type DocumentKind = 'resume' | 'supplement' | 'star'
export type DocumentVersion = { id: number; kind: DocumentKind; title: string; content_hash: string; is_current: boolean; created_at: string }
export type Document = DocumentVersion & { content_md: string }

/** 当前版本。还没保存过时是 null：先看有没有版本，没有就不去读当前版本（那会是一个 404） */
const versionsQuery = (kind: DocumentKind) => ({
  queryKey: ['documents', kind, 'versions'],
  queryFn: () => api<DocumentVersion[]>(`/documents/${kind}/versions`),
})

/** 当前版本。先看有没有版本（和历史版本共用一份缓存，不重复请求），没有就是 null */
export function useDocument(kind: DocumentKind) {
  const client = useQueryClient()
  return useQuery({
    queryKey: ['documents', kind, 'current'],
    queryFn: async () => {
      const versions = await client.fetchQuery(versionsQuery(kind))
      return versions.length ? api<Document>(`/documents/${kind}`) : null
    },
  })
}

export function useVersions(kind: DocumentKind) {
  return useQuery(versionsQuery(kind))
}

export function useVersion(kind: DocumentKind, id: number | null) {
  return useQuery({ queryKey: ['documents', kind, 'version', id], queryFn: () => api<Document>(`/documents/${kind}/versions/${id}`), enabled: id !== null })
}

/** 保存：每次保存是一个新版本；内容没变时后端返回 200 和当前版本，不新增 */
export function useSaveDocument(kind: DocumentKind) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (content_md: string) => {
      const { status, data } = await apiWithStatus<Document>(`/documents/${kind}`, sendJson('PUT', { content_md }))
      return { document: data, created: status === 201 }
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ['documents', kind] }),
  })
}
