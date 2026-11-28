"""设置页的配置：读、写，以及叠加到运行配置上。"""

from pydantic import SecretStr
from sqlmodel import Session

from app.core.config import Settings
from app.db.models import AppSettings, now_iso

KEEP = object()  # 这一项不改


def load(session: Session) -> AppSettings | None:
    return session.get(AppSettings, 1)


def save(session: Session, **changes) -> AppSettings:
    """保存设置页填的项。没传的项不改；Key 传 None 也表示沿用已经存的。"""
    row = load(session) or AppSettings()
    for field, value in changes.items():
        if value is KEEP or (field.endswith("api_key") and value is None):
            continue
        setattr(row, field, value)
    row.updated_at = now_iso()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def apply(settings: Settings, row: AppSettings | None) -> Settings:
    """设置页填过的项覆盖 .env 的值，返回新的运行配置。"""
    if row is None:
        return settings
    update: dict = {
        "hide_personal_info": row.hide_personal_info,
        "privacy_terms": row.privacy_terms,
    }
    if row.llm_base_url:
        update["llm_base_url"] = row.llm_base_url
    if row.llm_model:
        update["llm_model"] = row.llm_model
    if row.llm_api_key:
        update["llm_api_key"] = SecretStr(row.llm_api_key)
    if row.search_provider:
        update["search_provider"] = row.search_provider
    if row.search_api_key:
        update["search_api_key"] = SecretStr(row.search_api_key)
    return settings.model_copy(update=update)
