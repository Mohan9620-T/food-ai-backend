from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database.database import Base, engine
from app.models.user import User
from app.models.chat import ChatSession, ChatMessageRecord
from app.api.user_api import router as user_router
from app.api.chat import router as chat_router

app = FastAPI(
    title="Food AI Backend",
    version="1.0.0"
)

Base.metadata.create_all(bind=engine)

app.include_router(user_router)
app.include_router(chat_router)


@app.get("/")
def root():
    return {"message": "Food AI Backend Running"}


@app.get("/health")
def health():
    return {"status": "Healthy"}


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)