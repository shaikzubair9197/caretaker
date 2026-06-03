from sqlalchemy import DateTime
from datetime import datetime
from sqlalchemy import UniqueConstraint


from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, Integer, String,Text

class Base(DeclarativeBase):
    pass


class Task(Base):

    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True)

    description = Column(String, nullable=False)

    priority = Column(String, default="medium")

    status = Column(String, default="pending")



class ActiveWindow(Base):

    __tablename__ = "active_windows"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    window_title = Column(
        Text,
        nullable=False
    )

    started_at = Column(
        DateTime,
        nullable=False
    )

    ended_at = Column(
        DateTime,
        nullable=True
    )

    duration_seconds = Column(
        Integer,
        nullable=True
    )

    created_at = Column(
        DateTime,
    )

class Memory(Base):
    __tablename__ = "memories"

    id = Column(Integer, primary_key=True, index=True)

    text = Column(Text, nullable=False)

    type = Column(String, default="general")

    importance = Column(Integer, default=1)

    __table_args__ = (
        UniqueConstraint(
            "text",
            name="uq_memory_text"
        ),
    )