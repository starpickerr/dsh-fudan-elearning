import { checkMessages } from '../../../Alcor/packages/alcor-plugin-info/src/elearning-status.js'
import { z } from 'zod'
import { createTypertRemoteInfo } from '@deepseek-ai/dsh-typert-protocol/remote-info'

const state = z.object({ installed: z.boolean(), configured: z.boolean(), connected: z.boolean(), writable: z.boolean(), username: z.string().max(160).optional() }).strict()
export const schemas = {
  elearningAccount: [z.object({}).strict(), state],
  saveElearningAccount: [z.object({ username: z.string().trim().min(1).max(160), password: z.string().min(1).max(4096) }).strict(), state],
  removeElearningAccount: [z.object({}).strict(), state],
  checkElearning: [z.object({}).strict(), z.object({ status: z.enum(['ready', 'disconnected', 'login_required', 'query_failed']), courseCount: z.number().int().nonnegative().optional(), code: z.enum(Object.keys(checkMessages)).optional() }).strict()],
}
export const remote = createTypertRemoteInfo({ package: '@alcor/elearning', methods: Object.fromEntries(Object.entries(schemas).map(([name, [request, result]]) => [`alcorSchool/${name}`, {
  service: 'alcorSchool', invocation: { kind: 'direct' },
  parameters: [{ name: 'request', source: 'json', codec: { mode: 'strict', typeSymbol: `@alcor/elearning#${name}Request`, create: () => request } }],
  result: { mode: 'strict', typeSymbol: `@alcor/elearning#${name}Result`, create: () => result },
}])) }, z)
