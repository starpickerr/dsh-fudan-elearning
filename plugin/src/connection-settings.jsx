import { Notice } from '../../../Alcor/packages/alcor-plugin-info/src/notice.jsx'
import { Field } from '../../../Alcor/packages/alcor-plugin-info/src/field.jsx'
import React, { useEffect, useState } from 'react'
import { Button, Input } from '@deepseek-ai/dsh-client-ui-primitives'
import { checkMessage } from '../../../Alcor/packages/alcor-plugin-info/src/elearning-status.js'

const unwrap = async promise => { const result = await promise; if (!result.ok) throw new Error(result.error?.message || '操作失败，请重试。'); return result.value }

export function SchoolConnection({ api }) {
  const [account, setAccount] = useState(null)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [removing, setRemoving] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [noticeTone, setNoticeTone] = useState('success')
  const load = async () => { const value = await unwrap(api.elearningAccount({})); setAccount(value); setUsername(value.username || '') }
  async function run(work) { setBusy(true); setError(''); setNotice(''); setNoticeTone('success'); try { await work() } catch (failure) { setError(failure.message) } finally { setBusy(false) } }
  useEffect(() => { void run(load) }, [api])
  if (account && !account.installed) return null
  const savedPassword = account?.configured && username === (account.username || '')
  return <section className="alcor-connection-settings alcor-school-connection" aria-label="学校连接">
    <span className="alcor-school-account-state">{!account ? '正在读取…' : account.configured ? '账号已保存' : '未配置账号'}</span>
    <form autoComplete="off" onSubmit={event => { event.preventDefault(); const input = { username, password }; void run(async () => { await unwrap(api.saveElearningAccount(input)); setPassword(''); await load(); setNotice('账号已保存。') }) }}>
      <Field label="学工号"><Input required maxLength={160} autoComplete="off" disabled={busy || !account?.writable} value={username} onChange={event => { setUsername(event.target.value); setNotice('') }} /></Field>
      <Field label="eLearning 密码"><Input required type="password" maxLength={4096} autoComplete="new-password" disabled={busy || !account?.writable} placeholder={savedPassword ? '********' : ''} aria-description={savedPassword ? '密码已保存，输入新密码以替换。' : undefined} value={password} onChange={event => { setPassword(event.target.value); setNotice('') }} /></Field>
      <div className="alcor-actions"><Button type="submit" variant="primary" disabled={busy || !account?.writable || !username.trim() || !password}>保存账号</Button>{account?.configured && <Button type="button" variant="ghost" disabled={busy} onClick={() => setRemoving(true)}>移除账号</Button>}</div>
    </form>
    {removing && <div className="alcor-school-remove"><Notice tone="warning">移除账号会停止后续访问，历史记录保留。</Notice><div className="alcor-actions"><Button variant="outline" disabled={busy} onClick={() => run(async () => { await unwrap(api.removeElearningAccount({})); setPassword(''); await load(); setRemoving(false); setNotice('账号已移除，历史记录已保留。') })}>确认移除账号</Button><Button variant="ghost" disabled={busy} onClick={() => setRemoving(false)}>取消</Button></div></div>}
    <div className="alcor-school-test"><Button variant="outline" disabled={busy || !savedPassword || !!password} onClick={() => run(async () => { const result = await unwrap(api.checkElearning({})); setNoticeTone(result.status === 'ready' ? 'success' : 'warning'); setNotice(result.status === 'ready' ? `连接有效，可访问 ${result.courseCount} 门课程。` : checkMessage(result)) })}>测试连接</Button></div>
    {error && <Notice tone="error" role="alert">{error}</Notice>}{notice && <Notice tone={noticeTone} role="status">{notice}</Notice>}
  </section>
}
