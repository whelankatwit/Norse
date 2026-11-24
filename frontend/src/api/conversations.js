import { request } from './client.js'

export function listConversations() {
  return request('/conversations')
}

export function createConversation(title) {
  const trimmed = title?.trim() ?? ''
  const body = trimmed ? { title: trimmed } : {}
  return request('/conversations', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function getConversation(conversationId) {
  return request(`/conversations/${conversationId}`)
}

export function deleteConversation(conversationId) {
  return request(`/conversations/${conversationId}`, { method: 'DELETE' })
}

export function sendMessage(conversationId, message, idempotencyKey, { recover = false } = {}) {
  const body = { message, idempotency_key: idempotencyKey }
  if (recover) {
    body.recover = true
  }
  return request(`/conversations/${conversationId}/messages`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}
