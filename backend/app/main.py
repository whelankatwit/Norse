"""Norse API entrypoint."""

from fastapi import FastAPI

from app.api.chat import router as chat_router

app = FastAPI(title="Norse")
app.include_router(chat_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
