const baseUrl = import.meta.env.VITE_API_BASE_URL ?? ''

export async function request(path, options = {}) {
  const headers = { ...options.headers }
  if (options.body) {
    headers['Content-Type'] = 'application/json'
  }

  const response = await fetch(`${baseUrl}${path}`, { ...options, headers })
  if (response.status === 204) {
    return null
  }

  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = body?.detail
    const message =
      typeof detail === 'string'
        ? detail
        : typeof detail?.message === 'string'
          ? detail.message
          : `Request failed (${response.status})`
    const error = new Error(message)
    error.status = response.status
    error.code = typeof detail?.code === 'string' ? detail.code : ''
    throw error
  }

  return body
}
