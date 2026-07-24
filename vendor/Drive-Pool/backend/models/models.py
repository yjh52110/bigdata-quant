from datetime import datetime
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String
from database import Base


class DriveAccount(Base):
    __tablename__ = "drive_accounts"

    id = Column(Integer, primary_key=True, index=True)
    account_index = Column(Integer, unique=True, nullable=False)
    email = Column(String, nullable=True)
    refresh_token = Column(String, nullable=True)
    access_token = Column(String, nullable=True)
    token_expiry = Column(DateTime, nullable=True)
    is_connected = Column(Boolean, default=False, nullable=False)


class File(Base):
    __tablename__ = "files"

    id = Column(Integer, primary_key=True, index=True)
    file_name = Column(String, nullable=False)
    drive_file_id = Column(String, nullable=False)
    account_index = Column(Integer, nullable=False)
    size = Column(BigInteger, default=0)
    mime_type = Column(String, nullable=True)
    thumbnail_link = Column(String, nullable=True)
    parent_drive_file_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Profile(Base):
    __tablename__ = "profile"

    id = Column(Integer, primary_key=True)
    display_name = Column(String, nullable=True)
    bio = Column(String, nullable=True)
    avatar_drive_file_id = Column(String, nullable=True)
    avatar_account_index = Column(Integer, nullable=True)


class AppConfig(Base):
    __tablename__ = "app_config"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)
