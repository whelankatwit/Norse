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
import './App.css'

const STORAGE_KEY = 'norse.selectedConversationId'

export default function App() {
  const [conversations, setConversations] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [messagesById, setMessagesById] = useState({})
  const [pendingById, setPendingById] = useState({})
  const [sendingIds, setSendingIds] = useState({})
  const [errorsById, setErrorsById] = useState({})
  const [notesByMessageId, setNotesByMessageId] = useState({})
  const [memories, setMemories] = useState([])
  const [memoriesOpen, setMemoriesOpen] = useState(false)
  const openTokens = useRef({})
  const removedIds = useRef(new Set())
  const sendingRef = useRef(new Set())
  const transcriptRef = useRef(null)

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
      setMessagesById((current) => {
        const next = { ...current }
        delete next[conversationId]
        return next
      })
      setPendingById((current) => {
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

  async function handleSend(text) {
    const conversationId = selectedId
    if (!conversationId || sendingRef.current.has(conversationId)) {
      return
    }

    sendingRef.current.add(conversationId)
    setSendingIds((current) => ({ ...current, [conversationId]: true }))
    setPendingById((current) => ({ ...current, [conversationId]: text }))
    setErrorsById((current) => {
      const next = { ...current }
      delete next[conversationId]
      return next
    })

    try {
      const exchange = await sendMessage(conversationId, text)
      if (removedIds.current.has(conversationId)) {
        return
      }
      setMessagesById((current) => {
        const local = current[conversationId] ?? []
        const ids = new Set(local.map((message) => message.id))
        const next = [...local]
        if (!ids.has(exchange.user_message.id)) {
          next.push(exchange.user_message)
        }
        if (!ids.has(exchange.assistant_message.id)) {
          next.push(exchange.assistant_message)
        }
        return { ...current, [conversationId]: next }
      })
      const saved = exchange.memories_saved ?? []
      const conflicts = exchange.memory_conflicts ?? []
      const tools = exchange.tool_uses ?? []
      const requests = exchange.tool_requests ?? []
      const results = exchange.tool_results ?? []
      if (
        saved.length > 0 ||
        conflicts.length > 0 ||
        tools.length > 0 ||
        requests.length > 0 ||
        results.length > 0
      ) {
        setNotesByMessageId((current) => ({
          ...current,
          [exchange.assistant_message.id]: { saved, conflicts, tools, requests, results },
        }))
      }
      if (saved.length > 0) {
        try {
          await refreshMemories()
        } catch (requestError) {
          setErrorsById((current) => ({
            ...current,
            memories: requestError.message,
          }))
        }
      }
      rememberConversations(await listConversations())
    } catch (requestError) {
      if (!removedIds.current.has(conversationId)) {
        setErrorsById((current) => ({
          ...current,
          [conversationId]: requestError.message,
        }))
      }
    } finally {
      sendingRef.current.delete(conversationId)
      setSendingIds((current) => {
        const next = { ...current }
        delete next[conversationId]
        return next
      })
      setPendingById((current) => {
        const next = { ...current }
        delete next[conversationId]
        return next
      })
    }
  }

  const selected = conversations.find((conversation) => conversation.id === selectedId)
  const history = selectedId ? (messagesById[selectedId] ?? []) : []
  const pendingText = selectedId ? pendingById[selectedId] : ''
  const shown = pendingText
    ? [
        ...history,
        {
          id: `pending-${selectedId}`,
          role: 'user',
          content: pendingText,
        },
      ]
    : history
  const pageError = errorsById['']
  const lastMessageId = shown.at(-1)?.id ?? ''
  const savedNote = notesByMessageId[history.at(-1)?.id ?? '']
  const noteMark = savedNote
    ? [savedNote.saved?.length ?? 0, savedNote.conflicts?.length ?? 0, savedNote.tools?.length ?? 0].join(':')
    : ''

  useLayoutEffect(() => {
    const transcript = transcriptRef.current
    if (!transcript) {
      return
    }
    transcript.scrollTop = transcript.scrollHeight
  }, [selectedId, shown.length, lastMessageId, pendingText, sendingIds[selectedId], noteMark])

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
                waiting={Boolean(sendingIds[selectedId])}
                notesByMessageId={notesByMessageId}
              />
            </div>
            <Composer
              disabled={Boolean(sendingIds[selectedId])}
              onSend={handleSend}
            />
          </>
        ) : (
          <p className="empty">Select a conversation or create one.</p>
        )}
      </main>
    </div>
  )
}
