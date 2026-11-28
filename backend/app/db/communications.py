"""沟通记录：用户粘贴进来的招聘聊天内容，按岗位保存。"""

from sqlmodel import Session, col, select

from app.db.engine import serialized
from app.db.models import Communication, Speaker


@serialized
def add_communication(
    session: Session, job_id: int, speaker: Speaker, text: str
) -> tuple[Communication, bool]:
    """保存一条沟通记录，返回 (记录, 是否新增)。同一岗位下同一个人的同样内容只保存一次。"""
    text = text.strip()
    for existing in list_communications(session, job_id):
        if existing.speaker == speaker and existing.text == text:
            return existing, False
    record = Communication(job_id=job_id, speaker=speaker, text=text)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record, True


def list_communications(session: Session, job_id: int) -> list[Communication]:
    """一个岗位的全部沟通记录，按保存的先后排列。"""
    statement = (
        select(Communication).where(Communication.job_id == job_id).order_by(col(Communication.id))
    )
    return list(session.exec(statement).all())


def delete_communication(session: Session, job_id: int, communication_id: int) -> bool:
    record = session.get(Communication, communication_id)
    if record is None or record.job_id != job_id:
        return False
    session.delete(record)
    session.commit()
    return True
