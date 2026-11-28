import { createHash } from 'node:crypto'
import { readdir, readFile, writeFile } from 'node:fs/promises'
import { join } from 'node:path'

const root = process.cwd()
const files = []

async function walk(directory) {
  for (const entry of await readdir(join(root, directory), { withFileTypes: true })) {
    const path = join(directory, entry.name)
    if (entry.isDirectory()) await walk(path)
    else if (entry.isFile()) files.push(path.replaceAll('\\', '/'))
  }
}

await walk('src')
await walk('public')
for (const file of [
  'index.html', 'package.json', 'package-lock.json', 'vite.config.ts',
  'scripts/copy-font-licenses.mjs', 'scripts/write-build-hash.mjs',
]) files.push(file)
for (const entry of await readdir(root, { withFileTypes: true })) {
  if (entry.isFile() && /^tsconfig.*\.json$/.test(entry.name)) files.push(entry.name)
}

const lines = await Promise.all([...new Set(files)].sort().map(async (path) => {
  const digest = createHash('sha256').update(await readFile(join(root, path))).digest('hex')
  return `${digest}  ${path}`
}))
await writeFile(join(root, 'dist', '.build-hash'), `${lines.join('\n')}\n`, 'utf8')
