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
          </li>
        )
      })}
      {waiting ? <li className="waiting">Waiting for a reply…</li> : null}
    </ol>
  )
}
