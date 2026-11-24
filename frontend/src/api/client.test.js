import assert from 'node:assert/strict'
import path from 'node:path'
import { after, before, test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

const root = path.dirname(path.dirname(path.dirname(fileURLToPath(import.meta.url))))

let server
let request
let sendMessage

before(async () => {
  server = await createServer({
    configFile: false,
    root,
    server: { middlewareMode: true },
    appType: 'custom',
    logLevel: 'silent',
  })
  ;({ request } = await server.ssrLoadModule('/src/api/client.js'))
  ;({ sendMessage } = await server.ssrLoadModule('/src/api/conversations.js'))
})

after(async () => {
  await server.close()
})

function jsonResponse(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

async function catchRequest(response) {
  globalThis.fetch = async () => response
  try {
    await request('/conversations/one/messages', { method: 'POST', body: '{}' })
  } catch (error) {
    assert.equal(typeof error.message, 'string')
    assert.equal(error.message.includes('[object Object]'), false)
    return error
  }
  assert.fail('expected request to fail')
}

test('structured error shows the readable message', async () => {
  const error = await catchRequest(
    jsonResponse(502, {
      detail: { code: 'empty_reply', message: 'Ollama returned an empty reply.' },
    }),
  )
  assert.equal(error.message, 'Ollama returned an empty reply.')
  assert.equal(error.status, 502)
  assert.equal(error.code, 'empty_reply')
})

test('string errors stay text', async () => {
  const error = await catchRequest(jsonResponse(404, { detail: 'Conversation not found' }))
  assert.equal(error.message, 'Conversation not found')
  assert.equal(error.status, 404)
  assert.equal(error.code, '')
})

test('a missing body uses the status', async () => {
  const error = await catchRequest(new Response('', { status: 502 }))
  assert.equal(error.message, 'Request failed (502)')
  assert.equal(error.status, 502)
})

test('malformed bodies use the status', async () => {
  const broken = await catchRequest(
    new Response('{', { status: 500, headers: { 'Content-Type': 'application/json' } }),
  )
  assert.equal(broken.message, 'Request failed (500)')

  const list = await catchRequest(jsonResponse(422, { detail: [{ msg: 'Field required' }] }))
  assert.equal(list.message, 'Request failed (422)')

  const objectMessage = await catchRequest(
    jsonResponse(502, { detail: { code: 'empty_reply', message: { text: 'hidden' } } }),
  )
  assert.equal(objectMessage.message, 'Request failed (502)')
})

test('submission status codes stay distinct', async () => {
  for (const code of ['request_in_progress', 'request_failed', 'idempotency_conflict']) {
    const error = await catchRequest(jsonResponse(409, { detail: { code, message: code } }))
    assert.equal(error.code, code)
    assert.equal(error.status, 409)
    assert.equal(error.message, code)
    assert.equal(error.message.includes('[object Object]'), false)
  }
})

test('sendMessage posts one idempotency key and a read-only recovery flag', async () => {
  const bodies = []
  globalThis.fetch = async (_url, options) => {
    bodies.push(JSON.parse(options.body))
    return jsonResponse(200, { replayed: false })
  }
  await sendMessage('conversation-1', 'Hello', 'key-1')
  await sendMessage('conversation-1', 'Hello', 'key-1', { recover: true })
  assert.deepEqual(bodies[0], { message: 'Hello', idempotency_key: 'key-1' })
  assert.deepEqual(bodies[1], { message: 'Hello', idempotency_key: 'key-1', recover: true })
})
