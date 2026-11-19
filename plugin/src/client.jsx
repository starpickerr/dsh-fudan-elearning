import {withModeView} from '../../../Alcor/packages/alcor-plugin-info/src/mode-view.jsx'
import noticeStyle from '../../../Alcor/packages/alcor-plugin-info/src/notice.css'
import React from 'react'
import { remote } from './remote.js'
import { SchoolConnection } from './connection-settings.jsx'
import style from '../../../Alcor/packages/alcor-plugin-info/src/source-settings.css'
import connectionStyle from '../../../Alcor/packages/alcor-plugin-info/src/connection-settings.css'

export const inject = ['configForms', 'slots', 'remote']
function Settings({ api, view }) {
  if (view === 'summary') return '连接复旦课程、作业和公告。'
  return <section className="alcor-source-settings"><h2>复旦 eLearning</h2><SchoolConnection api={api} /></section>
}
export async function apply(ctx) {
  ctx.effect(() => { const element = document.createElement('style'); element.textContent = style + connectionStyle + noticeStyle; document.head.append(element); return () => element.remove() })
  await ctx.remote.$mount(remote)
  ctx.inject(['remote.alcorSchool'], inner => inner.slots.inject('plugins.bundle.config', () => inner.slots.register({ name: 'plugins.bundle.config', key: '@alcor/elearning', inject: () => ({ api: inner.remote.alcorSchool }) }, withModeView(inner,'@alcor/elearning',Settings))))
}
