/** 两段文字的字级对比：中文按字、英文和数字按整词，找出删掉的和新加的部分 */

export type Piece = { text: string; changed: boolean }

// 英文单词、数字、空白各算一个整体，其余每个字符一个
const TOKEN = /[A-Za-z0-9_.+#%-]+|\s+|[^]/gu
const MAX_CELLS = 4_000_000 // 再长就不逐字比了，整段标成改动

const tokens = (text: string) => text.match(TOKEN) ?? []

function pieces(parts: string[], keep: boolean[]): Piece[] {
  const out: Piece[] = []
  parts.forEach((text, i) => {
    const changed = !keep[i]
    const last = out[out.length - 1]
    if (last && last.changed === changed) last.text += text
    else out.push({ text, changed })
  })
  // 两处改动中间只隔一个字（常见是标点），并进改动里，读起来不碎
  for (let i = 1; i < out.length - 1; i++) {
    if (!out[i].changed && out[i].text.trim().length <= 1 && out[i - 1].changed && out[i + 1].changed) {
      out[i - 1].text += out[i].text + out[i + 1].text
      out.splice(i, 2)
      i--
    }
  }
  return out
}

/** before：原文，删掉的部分 changed；after：新文，新加的部分 changed */
export function diffText(before: string, after: string): { before: Piece[]; after: Piece[] } {
  const a = tokens(before)
  const b = tokens(after)
  const keepA = a.map(() => false)
  const keepB = b.map(() => false)

  // 先去掉相同的开头和结尾，中间再找最长公共子序列
  let start = 0
  while (start < a.length && start < b.length && a[start] === b[start]) { keepA[start] = keepB[start] = true; start++ }
  let endA = a.length
  let endB = b.length
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB--; keepA[endA] = keepB[endB] = true }

  const n = endA - start
  const m = endB - start
  if (n > 0 && m > 0 && n * m <= MAX_CELLS) {
    // lcs[i][j]：a[i..] 和 b[j..] 的最长公共子序列长度
    const width = m + 1
    const lcs = new Uint32Array((n + 1) * width)
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        lcs[i * width + j] = a[start + i] === b[start + j]
          ? lcs[(i + 1) * width + j + 1] + 1
          : Math.max(lcs[(i + 1) * width + j], lcs[i * width + j + 1])
      }
    }
    let i = 0
    let j = 0
    while (i < n && j < m) {
      if (a[start + i] === b[start + j]) { keepA[start + i] = keepB[start + j] = true; i++; j++ }
      else if (lcs[(i + 1) * width + j] >= lcs[i * width + j + 1]) i++
      else j++
    }
  }
  return { before: pieces(a, keepA), after: pieces(b, keepB) }
}
