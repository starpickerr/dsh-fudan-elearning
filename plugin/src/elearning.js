import { summarizeCoursePage } from './elearning-list.js'
import { createHash } from 'node:crypto'
import { Service } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import { z as schema } from 'zod'
import { credentialKey } from '@deepseek-ai/dsh-credentials'
import * as McpClient from '@deepseek-ai/dsh-mcp-client'
import { checkMessages } from '../../../Alcor/packages/alcor-plugin-info/src/elearning-status.js'

export const accountKey = credentialKey('alcor-elearning', 'account')
export const accountSchema = schema.object({ username: schema.string().trim().min(1).max(160), password: schema.string().min(1).max(4096) }).strict()

export async function checkConnection(ctx) {
  if (!ctx.tools.executable('mcp__elearning__list_courses')) return { status: 'disconnected' }
  const signal = AbortSignal.timeout(185000)
  try {
    const batch = await ctx.tools.execute({ signal, calls: [{ type: 'tool-call', id: 'alcor-elearning-check', name: 'mcp__elearning__list_courses', arguments: '{}' }] })
    if (signal.aborted) return { status: 'query_failed', code: 'timeout' }
    const result = batch.results[0]?.result
    if (result?.isError) {
      let code = 'query_failed'
      try {
        const detail = JSON.parse(result.error?.message)
        if (Object.hasOwn(checkMessages, detail?.error_code)) code = detail.error_code
      } catch {}
      return { status: code === 'login_required' ? 'login_required' : 'query_failed', code }
    }
    const courses = result?.value?.structuredContent?.value
    return Array.isArray(courses) ? { status: 'ready', courseCount: courses.length } : { status: 'query_failed' }
  } catch (error) { return { status: 'query_failed', code: signal.aborted || error?.name === 'TimeoutError' ? 'timeout' : 'query_failed' } }
}

export default class Elearning extends Service {
  static inject = ['credentials', 'authorization', 'tools']
  static Config = z.object({ command: z.string().required(), cwd: z.string().required(), dataDir: z.string().required(), args: z.array(z.string()).default(['-m', 'elearning_mcp.server']) })

  constructor(ctx, config) {
    super(ctx, 'alcorElearning')
    this.config = config
    this.pending = Promise.resolve()
    this.generation = 0
    this.active = null
    this.closed = false
  }

  async [Service.init]() {
    this.ctx.authorization.registerFlow({
      key: accountKey, label: '复旦 eLearning', methods: [{ id: 'password', label: '学工号和密码' }],
      async run(session) {
        const username = await session.prompt({ kind: 'text', message: '学工号' })
        const password = await session.prompt({ kind: 'secret', message: '密码' })
        await session.commit({ kind: 'grant', payload: accountSchema.parse({ username, password }) })
      },
    })
    this.ctx.on('tools/post-execute', summarizeCoursePage)
    this.ctx.on('tools/pre-execute', async (execution, next) => {
      const decision = await next()
      if (!execution.name.startsWith('mcp__elearning__')) return decision
      const generation = this.generation
      const record = await this.ctx.credentials.readRecord(accountKey)
      if (generation !== this.generation || !this.active || JSON.stringify(record) !== this.active) return { kind: 'deny', reason: 'eLearning 凭据已变化或尚未连接，请刷新连接。' }
      return decision
    })
    this.ctx.tools.guard(execution => {
      if (execution.name.startsWith('mcp__elearning__') && !this.active) return 'eLearning 凭据已变化或尚未连接，请刷新连接。'
    })
    this.ctx.on('credentials/record-updated', key => {
      if (key === accountKey) return this.refresh()
    })
    this.ctx.effect(() => async () => {
      this.closed = true
      this.generation++
      this.active = null
      await this.pending
      await this.connection?.dispose()
    })
    await this.refresh().catch(() => undefined)
  }

  refresh() {
    const generation = ++this.generation
    this.active = null
    const operation = this.pending.then(async () => {
      await this.connection?.dispose()
      this.connection = null
      if (this.closed || generation !== this.generation) return
      const record = await this.ctx.credentials.readRecord(accountKey)
      if (!record) return
      if (record.kind !== 'grant') throw new Error('eLearning 凭据格式无效，请重新输入。')
      const account = accountSchema.safeParse(record.payload)
      if (!account.success) throw new Error('eLearning 凭据格式无效，请重新输入。')
      // Keep secrets out of plugin configuration/settings; only the child transport receives them.
      this.connection = this.ctx.plugin(async inner => {
        await McpClient.apply(inner, McpClient.Config({
          transport: 'stdio', serverName: 'elearning', command: this.config.command,
          args: [...this.config.args, '--data-dir', this.config.dataDir, '--dsh-credentials'],
          cwd: this.config.cwd, env: { ALCOR_ELEARNING_CREDENTIAL: JSON.stringify(account.data) },
          toolCallTimeoutMs: 180000, failOnStartupError: true,
        }))
      })
      await this.connection.await()
      if (!this.closed && generation === this.generation) this.active = JSON.stringify(record)
    })
    this.pending = operation.catch(() => { this.ctx.logger.warn('eLearning 连接未就绪，请检查连接配置或重新保存账号。') })
    return operation
  }

  async state({ includeUsername = false } = {}) {
    const info = await this.ctx.credentials.describeRecord(accountKey)
    // Older independently installed clients strictly accept only the four status fields.
    const record = includeUsername && await this.ctx.credentials.readRecord(accountKey)
    const account = record?.kind === 'grant' && accountSchema.safeParse(record.payload)
    return { installed: true, configured: info.configured, connected: !!this.active, writable: info.writable, ...(account?.success ? { username: account.data.username } : {}) }
  }

  checkConnection() { return checkConnection(this.ctx) }

  async briefingSource() {
    if (!this.active) return null
    const record = await this.ctx.credentials.readRecord(accountKey)
    const account = record?.kind === 'grant' && accountSchema.safeParse(record.payload)
    if (!account?.success || JSON.stringify(record) !== this.active) return null
    const id = 'canvas-' + createHash('sha256').update('https://elearning.fudan.edu.cn|' + account.data.username).digest('hex').slice(0, 16)
    return { source: 'elearning', accountId: id, label: '复旦 eLearning', tool: 'mcp__elearning__fetch', method: 'fetch', arguments: {} }
  }

  async save(input) {
    const account = accountSchema.safeParse(input)
    if (!account.success) throw new Error('请填写有效的学工号和密码。')
    try {
      await this.ctx.authorization.begin({ key: accountKey, interaction: {
        notify() {}, prompt: async prompt => prompt.kind === 'secret' ? account.data.password : account.data.username,
      } })
      await this.pending
      return this.state()
    } catch { throw new Error('无法保存 eLearning 凭据，请检查 DSH 凭据库是否可写。') }
  }

  async remove() {
    this.ctx.authorization.cancel(accountKey)
    await this.ctx.credentials.deleteRecord(accountKey)
    await this.pending
    return this.state()
  }
}
