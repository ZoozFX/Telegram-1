from sqlalchemy import Column, Integer, String, DateTime, Text, func, ForeignKey
from .db import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(Integer, unique=True, index=True, nullable=False)
    lang = Column(String(2), default="en")  # 'en' or 'ar'
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class UserInput(Base):
    __tablename__ = "user_inputs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    text = Column(Text, nullable=False)
    tag = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
