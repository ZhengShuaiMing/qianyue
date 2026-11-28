import { copyFile, mkdir } from 'node:fs/promises'
import { join } from 'node:path'

const root = process.cwd()
const licenses = [
  ['@fontsource-variable/noto-sans-sc', 'OFL-1.1-Noto-Sans-SC.txt'],
  ['@fontsource/noto-serif-sc', 'OFL-1.1-Noto-Serif-SC.txt'],
]
const output = join(root, 'dist', 'third-party-licenses')
await mkdir(output, { recursive: true })
for (const [packageName, fileName] of licenses) {
  await copyFile(join(root, 'node_modules', packageName, 'LICENSE'), join(output, fileName))
}
