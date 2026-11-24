import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
  sendMessage,
} from './api/conversations.js'
import {
  createMemory,
  deleteMemory,
  listMemories,
  updateMemory,
} from './api/memories.js'
import Composer from './components/Composer.jsx'
import ConversationList from './components/ConversationList.jsx'
import MemoryPanel from './components/MemoryPanel.jsx'
import MessageList from './components/MessageList.jsx'
import {
  appendExchange,
  beginExecution,
  beginRecovery,
  createSubmission,
  notesForExchange,
  recoveryDelay,
  reduceSubmission,
} from './submission.js'
import './App.css'

const STORAGE_KEY = 'norse.selectedConversationId'

export default function App() {
  const [conversations, setConversations] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [messagesById, setMessagesById] = useState({})
  const [submissionsById, setSubmissionsById] = useState({})
  const [sendingIds, setSendingIds] = useState({})
  const [errorsById, setErrorsById] = useState({})
  const [notesByMessageId, setNotesByMessageId] = useState({})
  const [memories, setMemories] = useState([])
  const [memoriesOpen, setMemoriesOpen] = useState(false)
  const openTokens = useRef({})
  const removedIds = useRef(new Set())
  const sendingRef = useRef(new Set())
  const submissionsRef = useRef({})
  const timersRef = useRef({})
  const transcriptRef = useRef(null)

  useEffect(() => {
    const timers = timersRef.current
    return () => {
      for (const timer of Object.values(timers)) {
        clearTimeout(timer)
      }
    }
  }, [])

  useEffect(() => {
    let cancelled = false

    listConversations()
      .then((rows) => {
        if (cancelled) {
          return
        }
        rememberConversations(rows)
        const savedId = sessionStorage.getItem(STORAGE_KEY)
        if (savedId && rows.some((row) => row.id === savedId)) {
          openConversation(savedId)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setErrorsById((current) => ({ ...current, '': requestError.message }))
        }
      })

    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false

    listMemories()
      .then((rows) => {
        if (!cancelled) {
          setMemories(rows)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setErrorsById((current) => ({ ...current, memories: requestError.message }))
        }
      })

    return () => {
      cancelled = true
    }
  }, [])

  function rememberConversations(rows) {
    setConversations(rows.filter((row) => !removedIds.current.has(row.id)))
  }

  function rememberMessages(conversationId, serverMessages) {
    setMessagesById((current) => {
      const local = current[conversationId] ?? []
      const serverIds = new Set(serverMessages.map((message) => message.id))
      const onlyLocal = local.filter((message) => !serverIds.has(message.id))
      return { ...current, [conversationId]: [...serverMessages, ...onlyLocal] }
    })
  }

  async function openConversation(conversationId) {
    const token = (openTokens.current[conversationId] ?? 0) + 1
    openTokens.current[conversationId] = token
    setSelectedId(conversationId)
    sessionStorage.setItem(STORAGE_KEY, conversationId)

    try {
      const detail = await getConversation(conversationId)
      if (openTokens.current[conversationId] !== token) {
        return
      }
      rememberMessages(conversationId, detail.messages)
      if (!sendingRef.current.has(conversationId)) {
        setErrorsById((current) => {
          const next = { ...current }
          delete next[conversationId]
          return next
        })
      }
    } catch (requestError) {
      if (openTokens.current[conversationId] !== token) {
        return
      }
      setErrorsById((current) => ({
        ...current,
        [conversationId]: requestError.message,
      }))
    }
  }

  async function handleCreate(nextTitle) {
    try {
      const created = await createConversation(nextTitle)
      rememberConversations(await listConversations())
      await openConversation(created.id)
    } catch (requestError) {
      setErrorsById((current) => ({ ...current, '': requestError.message }))
    }
  }

  async function handleDelete(conversationId) {
    try {
      await deleteConversation(conversationId)
      removedIds.current.add(conversationId)
      openTokens.current[conversationId] = (openTokens.current[conversationId] ?? 0) + 1
      clearTimer(conversationId)
      rememberSubmission(conversationId, null)
      setBusy(conversationId, false)
      setMessagesById((current) => {
        const next = { ...current }
        delete next[conversationId]
        return next
      })
      if (selectedId === conversationId) {
        setSelectedId(null)
        sessionStorage.removeItem(STORAGE_KEY)
      }
      rememberConversations(await listConversations())
    } catch (requestError) {
      setErrorsById((current) => ({
        ...current,
        [conversationId]: requestError.message,
      }))
    }
  }

  async function refreshMemories() {
    setMemories(await listMemories())
    setErrorsById((current) => {
      const next = { ...current }
      delete next.memories
      return next
    })
  }

  async function handleCreateMemory(content) {
    await createMemory(content)
    await refreshMemories()
  }

  async function handleUpdateMemory(memoryId, content) {
    await updateMemory(memoryId, content)
    await refreshMemories()
  }

  async function handleDeleteMemory(memoryId) {
    await deleteMemory(memoryId)
    setMemories((current) => current.filter((memory) => memory.id !== memoryId))
  }

  function clearTimer(conversationId) {
    const timer = timersRef.current[conversationId]
    if (timer) {
      clearTimeout(timer)
      delete timersRef.current[conversationId]
    }
  }

  function rememberSubmission(conversationId, submission) {
    if (submission) {
      submissionsRef.current[conversationId] = submission
    } else {
      delete submissionsRef.current[conversationId]
    }
    setSubmissionsById({ ...submissionsRef.current })
  }

  function setBusy(conversationId, busy) {
    if (busy) {
      sendingRef.current.add(conversationId)
    } else {
      sendingRef.current.delete(conversationId)
    }
    setSendingIds((current) => {
      const next = { ...current }
      if (busy) {
        next[conversationId] = true
      } else {
        delete next[conversationId]
      }
      return next
    })
  }

  function schedulePoll(conversationId, attempt, delay) {
    clearTimer(conversationId)
    timersRef.current[conversationId] = setTimeout(() => {
      delete timersRef.current[conversationId]
      const submission = submissionsRef.current[conversationId]
      if (
        removedIds.current.has(conversationId) ||
        !submission ||
        submission.attempt !== attempt
      ) {
        return
      }
      void execute(conversationId, attempt, true)
    }, delay)
  }

  function settle(conversationId, attempt, outcome, mode) {
    if (removedIds.current.has(conversationId)) {
      clearTimer(conversationId)
      rememberSubmission(conversationId, null)
      setBusy(conversationId, false)
      return
    }
    const current = submissionsRef.current[conversationId]
    if (!current || current.attempt !== attempt) {
      return
    }
    const result = reduceSubmission(current, outcome, mode)
    if (result.action === 'done') {
      clearTimer(conversationId)
      rememberSubmission(conversationId, null)
      setBusy(conversationId, false)
      void acceptExchange(conversationId, result.exchange)
      return
    }
    rememberSubmission(conversationId, result.submission)
    if (result.action === 'poll') {
      schedulePoll(conversationId, attempt, result.delay)
      return
    }
    clearTimer(conversationId)
    setBusy(conversationId, false)
  }

  async function execute(conversationId, attempt, recover) {
    const submission = submissionsRef.current[conversationId]
    if (!submission || submission.attempt !== attempt || removedIds.current.has(conversationId)) {
      return
    }
    setBusy(conversationId, true)
    try {
      const exchange = await sendMessage(conversationId, submission.text, submission.key, { recover })
      settle(conversationId, attempt, { type: 'success', exchange }, recover ? 'recover' : 'execute')
    } catch (requestError) {
      settle(
        conversationId,
        attempt,
        { type: 'error', code: requestError.code || '', message: requestError.message },
        recover ? 'recover' : 'execute',
      )
    }
  }

  async function acceptExchange(conversationId, exchange) {
    if (removedIds.current.has(conversationId)) {
      return
    }
    setMessagesById((current) => ({
      ...current,
      [conversationId]: appendExchange(current[conversationId] ?? [], exchange),
    }))
    const notes = notesForExchange(exchange)
    if (notes) {
      setNotesByMessageId((current) => ({
        ...current,
        [exchange.assistant_message.id]: notes,
      }))
    }
    if ((exchange.memories_saved ?? []).length > 0) {
      try {
        await refreshMemories()
      } catch (requestError) {
        setErrorsById((current) => ({
          ...current,
          memories: requestError.message,
        }))
      }
    }
    try {
      rememberConversations(await listConversations())
    } catch (requestError) {
      setErrorsById((current) => ({ ...current, '': requestError.message }))
    }
  }

  async function handleSend(text) {
    const conversationId = selectedId
    if (
      !conversationId ||
      sendingRef.current.has(conversationId) ||
      submissionsRef.current[conversationId]
    ) {
      return
    }
    const submission = createSubmission(text, crypto.randomUUID())
    rememberSubmission(conversationId, submission)
    await execute(conversationId, submission.attempt, false)
  }

  function handleRetry(conversationId) {
    const current = submissionsRef.current[conversationId]
    if (!current || current.phase !== 'failed' || sendingRef.current.has(conversationId)) {
      return
    }
    const submission = beginExecution(current)
    rememberSubmission(conversationId, submission)
    void execute(conversationId, submission.attempt, false)
  }

  function handleCheckStatus(conversationId) {
    const current = submissionsRef.current[conversationId]
    if (!current || current.phase !== 'waiting' || sendingRef.current.has(conversationId)) {
      return
    }
    const submission = beginRecovery(current)
    rememberSubmission(conversationId, submission)
    setBusy(conversationId, true)
    schedulePoll(conversationId, submission.attempt, recoveryDelay(submission))
  }

  const selected = conversations.find((conversation) => conversation.id === selectedId)
  const history = selectedId ? (messagesById[selectedId] ?? []) : []
  const submission = selectedId ? submissionsById[selectedId] : null
  const pendingText = submission?.text ?? ''
  const shown = submission
    ? [
        ...history,
        {
          id: `pending-${submission.key}`,
          role: 'user',
          content: submission.text,
        },
      ]
    : history
  const pageError = errorsById['']
  const lastMessageId = shown.at(-1)?.id ?? ''
  const savedNote = notesByMessageId[history.at(-1)?.id ?? '']
  const noteMark = savedNote
    ? [savedNote.saved?.length ?? 0, savedNote.conflicts?.length ?? 0, savedNote.tools?.length ?? 0].join(':')
    : ''
  const showWaiting = submission?.phase === 'sending'
  const notice = submission?.phase === 'waiting' ? submission.status : ''
  const statusText =
    submission && submission.phase !== 'sending' && submission.phase !== 'waiting'
      ? submission.status
      : ''
  const sendingSelected = Boolean(selectedId && sendingIds[selectedId])
  const showRetry = submission?.phase === 'failed'
  const showCheck = submission?.phase === 'waiting' && !sendingSelected

  useLayoutEffect(() => {
    const transcript = transcriptRef.current
    if (!transcript) {
      return
    }
    transcript.scrollTop = transcript.scrollHeight
  }, [selectedId, shown.length, lastMessageId, pendingText, notice, sendingSelected, noteMark])

  return (
    <div className="shell">
      <ConversationList
        conversations={conversations}
        selectedId={selectedId}
        onSelect={openConversation}
        onCreate={handleCreate}
        onDelete={handleDelete}
        onOpenMemories={() => setMemoriesOpen(true)}
        sendingIds={sendingIds}
      />
      {memoriesOpen ? (
        <MemoryPanel
          memories={memories}
          loadError={errorsById.memories}
          onClose={() => setMemoriesOpen(false)}
          onCreate={handleCreateMemory}
          onUpdate={handleUpdateMemory}
          onDelete={handleDeleteMemory}
        />
      ) : null}
      <main className="chat">
        {pageError ? <p className="error">{pageError}</p> : null}
        {selectedId ? (
          <>
            <h2>{selected?.title ?? 'Conversation'}</h2>
            {errorsById[selectedId] ? (
              <p className="error">{errorsById[selectedId]}</p>
            ) : null}
            <div className="transcript" ref={transcriptRef}>
              <MessageList
                messages={shown}
                waiting={showWaiting}
                notice={notice}
                notesByMessageId={notesByMessageId}
              />
            </div>
            {statusText ? <p className="error">{statusText}</p> : null}
            {showRetry || showCheck ? (
              <div className="submission-actions">
                {showRetry ? (
                  <button type="button" className="primary" onClick={() => handleRetry(selectedId)}>
                    Retry
                  </button>
                ) : null}
                {showCheck ? (
                  <button type="button" onClick={() => handleCheckStatus(selectedId)}>
                    Check status again
                  </button>
                ) : null}
              </div>
            ) : null}
            <Composer disabled={Boolean(submission)} onSend={handleSend} />
          </>
        ) : (
          <p className="empty">Select a conversation or create one.</p>
        )}
      </main>
    </div>
  )
}
