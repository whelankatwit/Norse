import assert from 'node:assert/strict'
import test from 'node:test'
import {
  COULD_NOT_ANSWER,
  POLL_DELAYS_MS,
  STILL_GENERATING,
  appendExchange,
  applyOutcome,
  beginExecution,
  beginRecovery,
  createSubmission,
  notesForExchange,
  recoveryDelay,
  reduceSubmission,
} from './submission.js'

const inProgress = { type: 'error', code: 'request_in_progress', message: 'pending' }
const failed = { type: 'error', code: 'request_failed', message: 'failed' }
const generation = { type: 'error', code: 'empty_reply', message: 'Ollama returned an empty reply.' }

function exchange(replayed = false) {
  return {
    user_message: { id: 'user-1', role: 'user', content: 'Hello' },
    assistant_message: { id: 'assistant-1', role: 'assistant', content: 'The reply.' },
    memories_saved: [],
    memory_conflicts: [],
    tool_uses: replayed ? [] : [{ name: 'git_log', ok: true }],
    tool_results: replayed ? [] : [{ name: 'git_log', output: 'ab82957' }],
    tool_requests: [],
    replayed,
  }
}

test('a failed submission keeps the original message for Retry', () => {
  const submission = createSubmission('Hello', 'key-1')
  const result = reduceSubmission(submission, generation, 'execute')
  assert.equal(result.action, 'stop')
  assert.equal(result.submission.phase, 'failed')
  assert.equal(result.submission.text, 'Hello')
  assert.equal(result.submission.key, 'key-1')
  assert.equal(result.submission.status, COULD_NOT_ANSWER)
})

test('a successful retry keeps one key and one exchange', () => {
  const failedSubmission = reduceSubmission(
    createSubmission('Hello', 'key-1'),
    generation,
    'execute',
  ).submission
  const firstRetry = beginExecution(failedSubmission)
  const secondRetry = beginExecution({ ...firstRetry, phase: 'failed', status: COULD_NOT_ANSWER })
  assert.equal(firstRetry.key, 'key-1')
  assert.equal(secondRetry.key, 'key-1')
  assert.equal(firstRetry.text, 'Hello')
  assert.equal(secondRetry.text, 'Hello')
  assert.notEqual(firstRetry.attempt, secondRetry.attempt)

  const saved = exchange(false)
  const done = reduceSubmission(secondRetry, { type: 'success', exchange: saved }, 'execute')
  const once = appendExchange([], done.exchange)
  const twice = appendExchange(once, done.exchange)
  assert.equal(done.action, 'done')
  assert.equal(once.length, 2)
  assert.equal(twice.length, 2)
  assert.equal(notesForExchange(saved).tools.length, 1)
})

test('request_in_progress starts recovery polling on the same key', () => {
  const result = reduceSubmission(createSubmission('Hello', 'key-1'), inProgress, 'execute')
  assert.equal(result.action, 'poll')
  assert.equal(result.delay, POLL_DELAYS_MS[0])
  assert.equal(result.submission.phase, 'waiting')
  assert.equal(result.submission.key, 'key-1')
  assert.equal(result.submission.status, STILL_GENERATING)
})

test('a completed recovery replaces the pending submission once', () => {
  const waiting = reduceSubmission(createSubmission('Hello', 'key-1'), inProgress, 'execute').submission
  const saved = exchange(true)
  const done = reduceSubmission(waiting, { type: 'success', exchange: saved }, 'recover')
  const messages = appendExchange([], done.exchange)
  assert.equal(done.action, 'done')
  assert.equal(saved.replayed, true)
  assert.equal(messages.length, 2)
  assert.equal(appendExchange(messages, saved).length, 2)
  assert.equal(notesForExchange(saved), null)
})

test('a failed recovery enables Retry with the original key', () => {
  const waiting = reduceSubmission(createSubmission('Hello', 'key-1'), inProgress, 'execute').submission
  const result = reduceSubmission(waiting, failed, 'recover')
  const retry = beginExecution(result.submission)
  assert.equal(result.submission.phase, 'failed')
  assert.equal(result.submission.status, COULD_NOT_ANSWER)
  assert.equal(retry.phase, 'sending')
  assert.equal(retry.key, 'key-1')
  assert.equal(retry.text, 'Hello')
})

test('polling stops after five attempts', () => {
  let submission = reduceSubmission(createSubmission('Hello', 'key-1'), inProgress, 'execute').submission
  const delays = [POLL_DELAYS_MS[0]]
  for (let index = 0; index < 4; index += 1) {
    const result = reduceSubmission(submission, inProgress, 'recover')
    assert.equal(result.action, 'poll')
    delays.push(result.delay)
    submission = result.submission
  }
  const stopped = reduceSubmission(submission, inProgress, 'recover')
  assert.equal(stopped.action, 'stop')
  assert.equal(stopped.submission.phase, 'waiting')
  assert.equal(stopped.submission.polls, POLL_DELAYS_MS.length)
  assert.deepEqual(delays, POLL_DELAYS_MS)
})

test('check status again starts another recovery sequence', () => {
  let submission = reduceSubmission(createSubmission('Hello', 'key-1'), inProgress, 'execute').submission
  for (let index = 0; index < POLL_DELAYS_MS.length; index += 1) {
    submission = reduceSubmission(submission, inProgress, 'recover').submission
  }
  const again = beginRecovery(submission)
  assert.equal(again.key, 'key-1')
  assert.equal(again.text, 'Hello')
  assert.equal(again.polls, 0)
  assert.equal(again.phase, 'waiting')
  assert.equal(recoveryDelay(again), POLL_DELAYS_MS[0])
  const next = reduceSubmission(again, inProgress, 'recover')
  assert.equal(next.action, 'poll')
  assert.equal(next.delay, POLL_DELAYS_MS[1])
})

test('a response updates the conversation that sent it', () => {
  const submissions = {
    alpha: createSubmission('Hello', 'key-a'),
    beta: createSubmission('Other', 'key-b'),
  }
  const updated = applyOutcome(submissions, 'alpha', 1, inProgress, 'execute')
  assert.equal(updated.submissions.alpha.phase, 'waiting')
  assert.equal(updated.submissions.beta, submissions.beta)
  assert.equal(updated.submissions.beta.text, 'Other')
})

test('an older attempt cannot replace a newer submission', () => {
  const newer = beginExecution(createSubmission('Hello', 'key-1'))
  const ignored = applyOutcome({ alpha: newer }, 'alpha', 1, failed, 'recover')
  assert.equal(ignored.action, 'ignore')
  assert.equal(ignored.submissions.alpha.phase, 'sending')
  assert.equal(ignored.submissions.alpha.attempt, newer.attempt)
})

test('conflict and unexpected recovery errors do not become an execution retry', () => {
  const waiting = reduceSubmission(createSubmission('Hello', 'key-1'), inProgress, 'execute').submission
  const conflict = reduceSubmission(
    waiting,
    { type: 'error', code: 'idempotency_conflict', message: 'This submission key was already used for a different message.' },
    'recover',
  )
  const unexpected = reduceSubmission(
    waiting,
    { type: 'error', code: '', message: 'Request failed (500)' },
    'recover',
  )
  assert.equal(conflict.submission.phase, 'conflict')
  assert.equal(unexpected.submission.phase, 'waiting')
  assert.equal(unexpected.submission.status, 'Request failed (500)')
  assert.notEqual(conflict.submission.phase, 'failed')
  assert.notEqual(unexpected.submission.phase, 'failed')
})
