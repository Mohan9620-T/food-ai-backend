from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.database.database import get_db
from app.rate_limit import limiter
from app.repositories.code_repository import CodeRepositoryStore
from app.schemas.code_assistant import AgentQuestion, ConnectRepository
from app.services import code_assistant as service
from app.utils.auth_dependency import get_current_user


def enabled(response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    if not settings.ENABLE_CODE_ASSISTANT:
        raise HTTPException(404, "Code assistant is not enabled")


router = APIRouter(
    prefix="/code/repositories", tags=["Code assistant"], dependencies=[Depends(enabled)]
)
store = CodeRepositoryStore()


@router.get("")
def list_repositories(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return [service.describe(row) for row in store.list(db, int(user["sub"]))]


@router.post("", status_code=201)
def connect(
    body: ConnectRepository, db: Session = Depends(get_db), user: dict = Depends(get_current_user)
):
    service.repository_path(int(user["sub"]), body.path)
    return service.describe(store.create(db, int(user["sub"]), body.name, body.path))


@router.delete("/{repo_id}", status_code=204)
def disconnect(repo_id: int, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    db.delete(store.owned(db, int(user["sub"]), repo_id))
    db.commit()


@router.post("/{repo_id}/index")
@limiter.limit("5/minute")
def index(
    request: Request,
    repo_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return service.index_repository(db, store.owned(db, int(user["sub"]), repo_id))


@router.get("/{repo_id}/files")
def files(repo_id: int, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return sorted(store.owned(db, int(user["sub"]), repo_id).snapshot)


@router.get("/{repo_id}/file")
def read_file(
    repo_id: int,
    path: str = Query(max_length=1000),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    snapshot = store.owned(db, int(user["sub"]), repo_id).snapshot
    if path not in snapshot:
        raise HTTPException(404, "Indexed file not found")
    return {"path": path, "text": snapshot[path]}


@router.get("/{repo_id}/search")
def search(
    repo_id: int,
    q: str = Query(min_length=1, max_length=500),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return service.search(store.owned(db, int(user["sub"]), repo_id).snapshot, q)


@router.get("/{repo_id}/git/{view}")
def git(
    repo_id: int, view: str, db: Session = Depends(get_db), user: dict = Depends(get_current_user)
):
    return {"text": service.git_view(store.owned(db, int(user["sub"]), repo_id), view)}


@router.post("/{repo_id}/agent")
@limiter.limit("5/minute")
async def agent(
    request: Request,
    repo_id: int,
    body: AgentQuestion,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
    model=Depends(service.get_agent_model),
):
    row = store.owned(db, int(user["sub"]), repo_id)
    if row.status != "ready":
        raise HTTPException(409, "Index the repository first")
    return await service.run_agent(row, body.question, model)
