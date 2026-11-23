import { request } from './client.js'

export function listMemories() {
  return request('/memories')
}

export function createMemory(content) {
  return request('/memories', {
    method: 'POST',
    body: JSON.stringify({ content }),
  })
}

export function updateMemory(memoryId, content) {
  return request(`/memories/${memoryId}`, {
    method: 'PATCH',
    body: JSON.stringify({ content }),
  })
}

export function deleteMemory(memoryId) {
  return request(`/memories/${memoryId}`, { method: 'DELETE' })
}
