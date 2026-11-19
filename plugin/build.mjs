import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { cp, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { build } from 'esbuild'

const directory = import.meta.dirname
const repo = join(directory, '..')
const source = JSON.parse(await readFile(join(directory, 'source-lock.json'), 'utf8'))
const manifest = JSON.parse(await readFile(join(directory, 'package.json'), 'utf8'))
if (process.platform !== 'darwin' || process.arch !== 'arm64') throw new Error('The plugin package requires macOS Apple Silicon.')
const files = ['mcp_server.py', 'pyproject.toml', ...(await readdir(join(repo, source.module)))
  .filter(name => /\.(py|json|sql)$/.test(name)).map(name => source.module + '/' + name)].sort()
const sha256 = data => createHash('sha256').update(data).digest('hex')
const identity = (await Promise.all(files.map(async name => name + '\0' + sha256(await readFile(join(repo, name))) + '\n'))).join('')
if (sha256(identity) !== source.sourceSha256) throw new Error('The service sources differ from plugin/source-lock.json.')
const python = process.env.DSH_SOURCE_PYTHON || join(repo, '.venv/bin/python')
const metadata = JSON.parse(execFileSync(python, ['-B', '-c', 'import importlib.metadata as m,json,sysconfig; print(json.dumps({"versions":{n:m.version(n) for n in '+JSON.stringify(Object.keys(source.dependencies))+'},"license":sysconfig.get_path("stdlib")+"/LICENSE.txt"}))'], { encoding: 'utf8' }))
for (const [name, version] of Object.entries(source.dependencies)) {
  if (metadata.versions[name] !== version) throw new Error(`Expected ${name} ${version}, found ${metadata.versions[name]}.`)
}
const stage = await mkdtemp(join(tmpdir(), 'dsh-source-package-'))
try {
  execFileSync(python, ['-B', join(repo, 'build_mcp.py'), '--dist-dir', stage, '--build-dir', join(stage, 'build')], { stdio: 'inherit' })
  const server = join(directory, 'server')
  await rm(server, { recursive: true, force: true })
  // npm archives omit symlinks, including PyInstaller's linked runtime libraries.
  await cp(join(stage, source.binary), server, { recursive: true, dereference: true })
  await writeFile(join(server, 'source.json'), JSON.stringify(source, null, 2) + '\n')
  await mkdir(join(server, 'licenses'), { recursive: true })
  await cp(metadata.license, join(server, 'licenses/Python-LICENSE.txt'))
  await build({ entryPoints: [join(directory, 'src/index.ts')], outdir: join(directory, 'lib'), bundle: true,
    packages: 'external', format: 'esm', platform: 'node', target: 'node24' })
  const client = await build({ entryPoints: [join(directory, 'src/client.jsx')], bundle: true,
    external: ['react', 'react-dom', 'zod', '@deepseek-ai/cordis', '@deepseek-ai/dsh-client-ui-primitives'],
    loader: { '.css': 'text', '.svg': 'dataurl' }, format: 'cjs', platform: 'browser', write: false, target: 'es2022' })
  await writeFile(join(directory, 'lib/client.js'), `window.__ModuleLoader__.load({id:${JSON.stringify(manifest.name)},factory(require){const module={exports:{}};const exports=module.exports;\n${client.outputFiles[0].text}\nreturn module.exports;}});\n`)
} finally {
  await rm(stage, { recursive: true, force: true })
}
