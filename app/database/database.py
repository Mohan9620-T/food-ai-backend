from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config.settings import DATABASE_URL

# libpq otherwise waits indefinitely when a database port accepts TCP but the
# server never completes its handshake (for example, a stalled Docker service).
connect_args = (
    {"connect_timeout": 5} if make_url(DATABASE_URL).get_backend_name() == "postgresql" else {}
)
engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
