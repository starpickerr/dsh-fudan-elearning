import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync } from 'node:fs'
import { cp, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { build } from 'esbuild'

const directory = import.meta.dirname
const repo = join(directory, '..')
const source = JSON.parse(await readFile(join(directory, 'source-lock.json'), 'utf8'))
const manifest = JSON.parse(await readFile(join(directory, 'package.json'), 'utf8'))
const target = `${process.platform}-${process.arch}`
if (!['darwin-arm64', 'win32-x64'].includes(target)) {
  throw new Error(`Build the plugin on macOS Apple Silicon or Windows x64 (current: ${target}).`)
}
const alcor = process.env.ALCOR_SOURCE ? resolve(process.env.ALCOR_SOURCE) : resolve(repo, '../Alcor')
const sharedSources = {
  name: 'alcor-shared-sources',
  setup(build) {
    build.onResolve({ filter: /^\.\.\/\.\.\/\.\.\/Alcor\// }, ({ path }) => ({
      path: join(alcor, path.slice('../../../Alcor/'.length)),
    }))
  },
}
const files = ['mcp_server.py', 'pyproject.toml', ...(await readdir(join(repo, source.module)))
  .filter(name => /\.(py|json|sql)$/.test(name)).map(name => source.module + '/' + name)].sort()
const sha256 = data => createHash('sha256').update(data).digest('hex')
const identity = (await Promise.all(files.map(async name => {
  const content = await readFile(join(repo, name), 'utf8')
  return name + '\0' + sha256(content.replace(/\r\n/g, '\n')) + '\n'
}))).join('')
if (sha256(identity) !== source.sourceSha256) throw new Error('The service sources differ from plugin/source-lock.json.')
const executable = process.platform === 'win32' ? 'python.exe' : 'python'
const venvPython = join(repo, '.venv', process.platform === 'win32' ? 'Scripts' : 'bin', executable)
const condaPython = process.env.CONDA_PREFIX && join(process.env.CONDA_PREFIX,
  ...(process.platform === 'win32' ? [executable] : ['bin', executable]))
const python = process.env.DSH_SOURCE_PYTHON || condaPython || (existsSync(venvPython) ? venvPython : executable)
const metadata = JSON.parse(execFileSync(python, ['-B', '-c', `
import importlib.metadata as metadata, json, sys, sysconfig
from pathlib import Path
locations = [Path(sysconfig.get_path('stdlib')), Path(sys.base_prefix), Path(sys.prefix)]
licenses = (p / name for p in locations for name in ('LICENSE.txt', 'LICENSE_PYTHON.txt'))
license_path = next((path for path in licenses if path.is_file()), None)
if license_path is None:
    raise SystemExit('Cannot find the Python runtime license.')
versions = {name: metadata.version(name) for name in ${JSON.stringify(Object.keys(source.dependencies))}}
print(json.dumps({'versions': versions, 'license': str(license_path)}))
`], { encoding: 'utf8' }))
for (const [name, version] of Object.entries(source.dependencies)) {
  if (metadata.versions[name] !== version) throw new Error(`Expected ${name} ${version}, found ${metadata.versions[name]}.`)
}
const stage = await mkdtemp(join(tmpdir(), 'dsh-source-package-'))
try {
  execFileSync(python, ['-B', join(repo, 'build_mcp.py'), '--dist-dir', stage, '--build-dir', join(stage, 'build')], { stdio: 'inherit' })
  const server = join(directory, 'server')
  await rm(server, { recursive: true, force: true })
  if (process.platform === 'win32') {
    // The one-file bootloader extracts DLLs under TEMP, away from pnpm's nested install path.
    await mkdir(server, { recursive: true })
    const binary = source.binary + '.exe'
    await cp(join(stage, binary), join(server, binary))
  } else {
    // npm archives omit symlinks, including PyInstaller's linked runtime libraries.
    await cp(join(stage, source.binary), server, { recursive: true, dereference: true })
  }
  await writeFile(join(server, 'source.json'), JSON.stringify(source, null, 2) + '\n')
  await writeFile(join(server, 'platform.json'), JSON.stringify({ target }) + '\n')
  await mkdir(join(server, 'licenses'), { recursive: true })
  await cp(metadata.license, join(server, 'licenses/Python-LICENSE.txt'))
  await build({ entryPoints: [join(directory, 'src/index.ts')], outdir: join(directory, 'lib'), bundle: true,
    plugins: [sharedSources], packages: 'external', format: 'esm', platform: 'node', target: 'node24' })
  const client = await build({ entryPoints: [join(directory, 'src/client.jsx')], bundle: true,
    external: ['react', 'react-dom', 'zod', '@deepseek-ai/cordis', '@deepseek-ai/dsh-client-ui-primitives'],
    plugins: [sharedSources], loader: { '.css': 'text', '.svg': 'dataurl' }, format: 'cjs', platform: 'browser', write: false, target: 'es2022' })
  await writeFile(join(directory, 'lib/client.js'), `window.__ModuleLoader__.load({id:${JSON.stringify(manifest.name)},factory(require){const module={exports:{}};const exports=module.exports;\n${client.outputFiles[0].text}\nreturn module.exports;}});\n`)
} finally {
  await rm(stage, { recursive: true, force: true })
}
