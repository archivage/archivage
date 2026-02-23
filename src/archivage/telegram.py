"""
Telegram client — Telethon wrapper for auth and message retrieval.
"""

import json
from pathlib import Path

from .config import getTelegramSession
from .log import logger


CREDS_PATH = Path.home() / '.config/archivage/telegram/credentials.json'


# ────────────
# Credentials

def loadCredentials() -> dict | None:
    if not CREDS_PATH.exists():
        return None
    with open(CREDS_PATH) as f:
        return json.load(f)


def saveCredentials(api_id: int, api_hash: str):
    CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CREDS_PATH, 'w') as f:
        json.dump({'api_id': api_id, 'api_hash': api_hash}, f, indent=2)
    CREDS_PATH.chmod(0o600)


# ────────────
# Text flattening

def flattenText(text) -> str:
    """Flatten export text field to plain string.

    Text is either a plain string or a list of strings and
    {"type": ..., "text": ...} entity dicts.
    """
    if isinstance(text, str):
        return text
    if not isinstance(text, list):
        return str(text) if text else ''
    return ''.join(
        part if isinstance(part, str) else part.get('text', '')
        for part in text
    )


# ────────────
# Import from Telegram Desktop export

def _normalizeExportId(chat_id: int, chat_type: str) -> int:
    """Convert export chat IDs to Telethon convention.

    Export uses bare positive IDs. Telethon uses:
    - personal_chat / bot_chat / saved_messages: positive (same)
    - private_supergroup / private_channel:      -100{id}
    - private_group:                             -{id}
    """
    if chat_type in ('private_supergroup', 'private_channel'):
        return -1000000000000 - chat_id
    if chat_type == 'private_group':
        return -chat_id
    return chat_id


def parseExport(path: Path) -> list[dict]:
    """Parse result.json from Telegram Desktop export.

    Returns flat list of chat dicts (chats + left_chats) with keys:
    id, name, type, messages (list of parsed message dicts).
    """
    with open(path) as f:
        data = json.load(f)

    chats = []
    for section in ('chats', 'left_chats'):
        chat_list = data.get(section, {}).get('list', [])
        for chat in chat_list:
            chat_type = chat.get('type', '')
            parsed = {
                'id':   _normalizeExportId(chat['id'], chat_type),
                'name': chat.get('name', ''),
                'type': chat_type,
                'messages': [],
            }
            for msg in chat.get('messages', []):
                parsed['messages'].append({
                    'id':        msg['id'],
                    'date':      msg.get('date', ''),
                    'from_id':   msg.get('from_id', ''),
                    'from_name': msg.get('from', ''),
                    'text':      flattenText(msg.get('text', '')),
                    'reply_to':  msg.get('reply_to_message_id'),
                    'type':      msg.get('type', 'message'),
                    'raw':       json.dumps(msg, ensure_ascii=False),
                })
            chats.append(parsed)

    return chats


# ────────────
# Telethon client

def createClient(api_id: int, api_hash: str):
    """Create a TelegramClient (not started yet)."""
    from telethon import TelegramClient
    session_path = str(getTelegramSession())
    Path(session_path).parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(session_path, api_id, api_hash)


async def authenticate(client):
    """Interactive auth: phone number + code."""
    await client.start()
    me = await client.get_me()
    logger.info(f"Authenticated as {me.first_name} (id={me.id})")
    return me


def _parseApiMessage(msg) -> dict:
    from telethon.tl.types import User
    from_id = None
    from_name = None
    if msg.sender:
        from_id = str(msg.sender_id)
        if isinstance(msg.sender, User):
            parts = [msg.sender.first_name or '', msg.sender.last_name or '']
            from_name = ' '.join(p for p in parts if p)
        else:
            from_name = getattr(msg.sender, 'title', None)
    return {
        'id':        msg.id,
        'date':      msg.date.strftime('%Y-%m-%dT%H:%M:%S') if msg.date else '',
        'from_id':   from_id,
        'from_name': from_name,
        'text':      msg.text or '',
        'reply_to':  msg.reply_to_msg_id if msg.reply_to else None,
        'type':      'message' if not msg.action else 'service',
        'raw':       json.dumps(msg.to_dict(), ensure_ascii=False, default=str),
    }


async def iterMessages(client, chat_id: int, min_id: int = 0, batch_size: int = 500):
    """Yield batches of parsed messages from a chat newer than min_id."""
    batch = []
    async for msg in client.iter_messages(chat_id, min_id=min_id):
        batch.append(_parseApiMessage(msg))
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


async def downloadMedia(client, chat_id: int, msg_id: int, output: Path):
    """Download media from a specific message to output path."""
    msgs = await client.get_messages(chat_id, ids=msg_id)
    if not msgs or not msgs.media:
        logger.info(f"Message {msg_id} in {chat_id}: no media, skipping")
        return None
    path = await client.download_media(msgs, file=str(output))
    if path:
        logger.info(f"Downloaded media to {path}")
    return path


async def fetchDialogs(client):
    """Return list of (id, name, type_str, top_msg_id) for all dialogs.

    top_msg_id lets callers skip chats with no new messages.
    """
    from telethon.tl.types import User, Chat, Channel
    dialogs = []
    async for d in client.iter_dialogs():
        entity = d.entity
        if isinstance(entity, User):
            t = 'personal_chat'
        elif isinstance(entity, Channel):
            t = 'private_supergroup' if entity.megagroup else 'channel'
        elif isinstance(entity, Chat):
            t = 'private_group'
        else:
            t = 'unknown'
        top_id = d.message.id if d.message else 0
        dialogs.append((d.id, d.name, t, top_id))
    return dialogs
