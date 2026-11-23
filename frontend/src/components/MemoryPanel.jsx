import { useState } from 'react'

const MAX_LENGTH = 500

export default function MemoryPanel({
  memories,
  loadError,
  onClose,
  onCreate,
  onUpdate,
  onDelete,
}) {
  const [draft, setDraft] = useState('')
  const [editingId, setEditingId] = useState(null)
  const [editText, setEditText] = useState('')
  const [error, setError] = useState('')

  async function handleCreate(event) {
    event.preventDefault()
    const content = draft.trim()
    if (!content) {
      return
    }
    setError('')
    try {
      await onCreate(content)
      setDraft('')
    } catch (requestError) {
      setError(requestError.message)
    }
  }

  function startEdit(memory) {
    setEditingId(memory.id)
    setEditText(memory.content)
    setError('')
  }

  async function handleUpdate(event) {
    event.preventDefault()
    const content = editText.trim()
    if (!content || !editingId) {
      return
    }
    setError('')
    try {
      await onUpdate(editingId, content)
      setEditingId(null)
      setEditText('')
    } catch (requestError) {
      setError(requestError.message)
    }
  }

  async function handleDelete(memory) {
    const confirmed = window.confirm('Delete this memory?')
    if (!confirmed) {
      return
    }
    setError('')
    try {
      await onDelete(memory.id)
      if (editingId === memory.id) {
        setEditingId(null)
        setEditText('')
      }
    } catch (requestError) {
      setError(requestError.message)
    }
  }

  return (
    <section className="memory-panel" aria-label="Memories">
      <div className="memory-header">
        <h2>Memories</h2>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
      {error || loadError ? <p className="error">{error || loadError}</p> : null}
      <form className="memory-form" onSubmit={handleCreate}>
        <textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          maxLength={MAX_LENGTH}
          rows={3}
          placeholder="Add a fact"
          aria-label="New memory"
        />
        <button className="primary" type="submit" disabled={!draft.trim()}>
          Add
        </button>
      </form>
      {memories.length === 0 ? <p className="empty">No memories yet.</p> : null}
      <ul className="memory-list">
        {memories.map((memory) => (
          <li key={memory.id}>
            {editingId === memory.id ? (
              <form onSubmit={handleUpdate}>
                <textarea
                  value={editText}
                  onChange={(event) => setEditText(event.target.value)}
                  maxLength={MAX_LENGTH}
                  rows={3}
                  aria-label="Edit memory"
                />
                <div className="memory-actions">
                  <button type="submit" disabled={!editText.trim()}>
                    Save
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setEditingId(null)
                      setEditText('')
                    }}
                  >
                    Cancel
                  </button>
                </div>
              </form>
            ) : (
              <>
                <p>{memory.content}</p>
                <div className="memory-actions">
                  <button type="button" onClick={() => startEdit(memory)}>
                    Edit
                  </button>
                  <button type="button" onClick={() => handleDelete(memory)}>
                    Delete
                  </button>
                </div>
              </>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
