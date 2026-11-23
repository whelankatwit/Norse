"""Norse API entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.memories import router as memories_router
from app.database.sqlite import init_db


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(title="Norse", lifespan=lifespan)
app.include_router(chat_router)
app.include_router(conversations_router)
app.include_router(memories_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
