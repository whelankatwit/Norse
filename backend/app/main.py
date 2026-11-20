"""Norse API entrypoint."""

from fastapi import FastAPI

app = FastAPI(title="Norse")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
