function toolLine(tool) {
  const status = tool.ok ? 'Used' : 'Failed'
  if (tool.workspace && tool.path) {
    return `${status} ${tool.name}: ${tool.workspace}/${tool.path}`
  }
  return `${status} ${tool.name}`
}

function requestLine(request) {
  if (request.status === 'succeeded') {
    return `Used ${request.name}`
  }
  if (request.status === 'failed') {
    return `Failed ${request.name}`
  }
  return `${request.name} was not invoked`
}

export default function MessageList({ messages, waiting, notesByMessageId = {} }) {
  if (messages.length === 0 && !waiting) {
    return <p className="empty">No messages yet.</p>
  }

  return (
    <ol className="messages">
      {messages.map((message) => {
        const note = notesByMessageId[message.id]
        return (
          <li key={message.id} className={`message ${message.role}`}>
            <span className="role">{message.role === 'assistant' ? 'Norse' : 'User'}</span>
            <p>{message.content}</p>
            {note?.saved.map((memory) => (
              <p key={memory.id} className="memory-note">
                Saved: {memory.content}
              </p>
            ))}
            {note?.conflicts.map((conflict) => (
              <p key={`${conflict.memory_id}:${conflict.stated}`} className="memory-note">
                Kept “{conflict.existing}”. New statement “{conflict.stated}” was not saved.
              </p>
            ))}
            {(note?.requests ?? []).map((request) => (
              <p key={request.name} className="memory-note">
                {requestLine(request)}
              </p>
            ))}
            {(note?.tools ?? [])
              .filter((tool) => !(note.requests ?? []).some((request) => request.name === tool.name))
              .map((tool, index) => (
                <p key={`${tool.name}:${tool.path ?? ''}:${index}`} className="memory-note">
                  {toolLine(tool)}
                </p>
              ))}
            {(note?.results ?? []).map((result, index) => (
              <pre key={`${result.name}:${index}`} className="tool-result">
                {`${result.name}\n${result.output}`}
              </pre>
            ))}
          </li>
        )
      })}
      {waiting ? <li className="waiting">Waiting for a reply…</li> : null}
    </ol>
  )
}
