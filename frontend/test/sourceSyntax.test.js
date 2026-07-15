import { readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import test from 'node:test'
import { transform } from 'esbuild'

async function sourceFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  const nested = await Promise.all(entries.map(async (entry) => {
    const target = path.join(directory, entry.name)
    if (entry.isDirectory()) return sourceFiles(target)
    return /\.(js|jsx)$/.test(entry.name) ? [target] : []
  }))
  return nested.flat()
}

test('all frontend JavaScript and JSX sources parse', async () => {
  const files = await sourceFiles(path.resolve('src'))
  await Promise.all(files.map(async (file) => {
    const source = await readFile(file, 'utf8')
    await transform(source, {
      loader: file.endsWith('.jsx') ? 'jsx' : 'js',
      jsx: 'automatic',
      sourcefile: file,
    })
  }))
})
