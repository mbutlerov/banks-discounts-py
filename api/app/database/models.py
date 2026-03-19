from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String
from app.database.base import Base

class Bank(Base):
    __tablename__ = "banks"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)