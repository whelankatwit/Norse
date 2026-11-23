import { useState } from 'react'

export default function ConversationList({
  conversations,
  selectedId,
  onSelect,
  onCreate,
  onDelete,
  onOpenMemories,
  sendingIds = {},
}) {
  const [title, setTitle] = useState('')

  function handleCreate(event) {
    event.preventDefault()
    const nextTitle = title
    setTitle('')
    onCreate(nextTitle)
  }

  function handleDelete(conversation) {
    const confirmed = window.confirm(`Delete “${conversation.title}”?`)
    if (confirmed) {
      onDelete(conversation.id)
    }
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <h1>Norse</h1>
        <button type="button" onClick={onOpenMemories}>
          Memories
        </button>
      </div>
      <form className="new-conversation" onSubmit={handleCreate}>
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="New conversation"
          aria-label="Conversation title"
        />
        <button className="primary" type="submit">Create</button>
      </form>
      {conversations.length === 0 ? <p>No conversations yet.</p> : null}
      <ul className="conversation-list">
        {conversations.map((conversation) => (
          <li key={conversation.id}>
            <button
              type="button"
              className="conversation"
              aria-current={conversation.id === selectedId ? 'true' : undefined}
              onClick={() => onSelect(conversation.id)}
            >
              {conversation.title}
              {sendingIds[conversation.id] ? ' · sending' : ''}
            </button>
            <button
              type="button"
              className="delete"
              onClick={() => handleDelete(conversation)}
            >
              Delete
            </button>
          </li>
        ))}
      </ul>
    </aside>
  )
}
