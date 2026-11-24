export const POLL_DELAYS_MS = [1000, 2000, 4000, 8000, 8000]
export const STILL_GENERATING = 'Norse is still generating a response.'
export const COULD_NOT_ANSWER = 'Norse could not answer.'

const GENERATION_CODES = new Set([
  'empty_reply',
  'invalid_ollama_response',
  'ollama_http_error',
  'ollama_timeout',
  'ollama_unavailable',
  'ollama_model_not_found',
])

export function createSubmission(text, key) {
  return {
    text,
    key,
    attempt: 1,
    phase: 'sending',
    polls: 0,
    status: '',
  }
}

export function beginExecution(submission) {
  return {
    ...submission,
    attempt: submission.attempt + 1,
    phase: 'sending',
    polls: 0,
    status: '',
  }
}

export function beginRecovery(submission) {
  return {
    ...submission,
    attempt: submission.attempt + 1,
    phase: 'waiting',
    polls: 0,
    status: STILL_GENERATING,
  }
}

export function recoveryDelay(submission) {
  return POLL_DELAYS_MS[submission.polls] ?? null
}

export function reduceSubmission(submission, outcome, mode) {
  if (outcome.type === 'success') {
    return { submission, action: 'done', exchange: outcome.exchange }
  }

  const code = outcome.code || ''
  if (code === 'request_in_progress') {
    const completed = mode === 'recover' ? submission.polls + 1 : 0
    if (mode === 'recover' && completed >= POLL_DELAYS_MS.length) {
      return {
        submission: { ...submission, phase: 'waiting', polls: completed, status: STILL_GENERATING },
        action: 'stop',
      }
    }
    return {
      submission: { ...submission, phase: 'waiting', polls: completed, status: STILL_GENERATING },
      action: 'poll',
      delay: POLL_DELAYS_MS[completed],
    }
  }

  if (code === 'request_failed' || (mode === 'execute' && (code === '' || GENERATION_CODES.has(code)))) {
    return {
      submission: { ...submission, phase: 'failed', status: COULD_NOT_ANSWER },
      action: 'stop',
    }
  }

  if (code === 'idempotency_conflict') {
    return {
      submission: {
        ...submission,
        phase: 'conflict',
        status: outcome.message || 'This submission key was already used for a different message.',
      },
      action: 'stop',
    }
  }

  return {
    submission: {
      ...submission,
      phase: 'waiting',
      status: outcome.message || 'The status check failed.',
    },
    action: 'stop',
  }
}

export function applyOutcome(submissions, conversationId, attempt, outcome, mode) {
  const current = submissions[conversationId]
  if (!current || current.attempt !== attempt) {
    return { submissions, action: 'ignore' }
  }
  const result = reduceSubmission(current, outcome, mode)
  if (result.action === 'done') {
    const next = { ...submissions }
    delete next[conversationId]
    return { ...result, submissions: next }
  }
  return {
    ...result,
    submissions: { ...submissions, [conversationId]: result.submission },
  }
}

export function appendExchange(messages, exchange) {
  const ids = new Set(messages.map((message) => message.id))
  const next = [...messages]
  if (exchange.user_message && !ids.has(exchange.user_message.id)) {
    next.push(exchange.user_message)
  }
  if (exchange.assistant_message && !ids.has(exchange.assistant_message.id)) {
    next.push(exchange.assistant_message)
  }
  return next
}

export function notesForExchange(exchange) {
  const saved = exchange.memories_saved ?? []
  const conflicts = exchange.memory_conflicts ?? []
  const tools = exchange.tool_uses ?? []
  const requests = exchange.tool_requests ?? []
  const results = exchange.tool_results ?? []
  if (
    saved.length === 0 &&
    conflicts.length === 0 &&
    tools.length === 0 &&
    requests.length === 0 &&
    results.length === 0
  ) {
    return null
  }
  return { saved, conflicts, tools, requests, results }
}
