import { useState } from 'react'

export default function Composer({ disabled, onSend }) {
  const [text, setText] = useState('')
  const trimmed = text.trim()

  function handleSubmit(event) {
    event.preventDefault()
    if (!trimmed || disabled) {
      return
    }
    setText('')
    onSend(trimmed)
  }

  function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      event.currentTarget.form?.requestSubmit()
    }
  }

  return (
    <form className="composer" onSubmit={handleSubmit}>
      <textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Write a message"
        aria-label="Message"
        rows={3}
        disabled={disabled}
      />
      <button className="primary" type="submit" disabled={disabled || !trimmed}>
        Send
      </button>
    </form>
  )
}
