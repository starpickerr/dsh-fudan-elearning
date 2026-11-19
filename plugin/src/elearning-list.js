export async function summarizeCoursePage(exec, result, next) {
  const decision = await next()
  if (exec.name !== 'mcp__elearning__fetch' || !exec.agent || exec.arguments?.limit === 1 ||
      result.isError || decision.kind !== 'accept' || 'content' in decision || 'value' in decision) return decision
  const page = result.value?.structuredContent?.value
  if (!Array.isArray(page?.source_items)) return decision
  const source_items = page.source_items.map(({ source_id, title, snippet, occurred_at, url, payload = {} }) => ({
    source_id, title, snippet: Array.from(snippet ?? '').slice(0, 120).join(''), occurred_at, url,
    course_id: payload.course_id, course_name: payload.course_name, entity_id: payload.entity_id,
    resource_type: payload.resource_type, due_at: payload.due_at, submission_status: payload.submission_status,
    score: payload.score, grade: payload.grade, points_possible: payload.points_possible,
  }))
  return { ...decision, content: [{ type: 'text', text: JSON.stringify({ value: { ...page, source_items } }) }] }
}
