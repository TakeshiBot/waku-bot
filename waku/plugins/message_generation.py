"""Handle native Stop updates without loading models or the Telegram agent."""

from pyrogram import Client, raw

from waku.plugins.agent.generation import stop_generation


@Client.on_raw_update(group=-99)
async def generation_stopped(client, update, users, chats):
    if not isinstance(update, raw.types.UpdateUserTyping):
        return
    if not isinstance(update.action, raw.types.SendMessageStopDraftAction):
        return
    stop_generation(
        client,
        update.user_id,
        update.top_msg_id,
        update.action.random_id,
        getattr(update, "business_connection_id", None),
    )
