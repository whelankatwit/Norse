"""Memory management routes."""

from fastapi import APIRouter, HTTPException

from app.database.sqlite import (
    DuplicateMemoryError,
    MemoryNotFoundError,
    create_memory,
    delete_memory,
    list_memories,
    update_memory,
)
from app.models.memories import MemoryCreate, MemoryRecord, MemoryUpdate

router = APIRouter()


@router.get("/memories", response_model=list[MemoryRecord])
def list_memories_route() -> list[MemoryRecord]:
    return [MemoryRecord.model_validate(row) for row in list_memories()]


@router.post("/memories", response_model=MemoryRecord)
def create_memory_route(body: MemoryCreate) -> MemoryRecord:
    try:
        row = create_memory(body.content)
    except DuplicateMemoryError as exc:
        raise HTTPException(status_code=409, detail="Memory already exists") from exc
    return MemoryRecord.model_validate(row)


@router.patch("/memories/{memory_id}", response_model=MemoryRecord)
def update_memory_route(memory_id: str, body: MemoryUpdate) -> MemoryRecord:
    try:
        row = update_memory(memory_id, body.content)
    except MemoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Memory not found") from exc
    except DuplicateMemoryError as exc:
        raise HTTPException(status_code=409, detail="Memory already exists") from exc
    return MemoryRecord.model_validate(row)


@router.delete("/memories/{memory_id}", status_code=204)
def delete_memory_route(memory_id: str) -> None:
    if not delete_memory(memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")
