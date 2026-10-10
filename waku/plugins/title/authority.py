"""Fresh native authority for tags; changing a label never grants admin rights."""

import inspect
import unicodedata

from pyrogram import Client, enums, raw


class TitleDenied(ValueError):
    pass


def has_right(member, name):
    return member.status == enums.ChatMemberStatus.OWNER or (
        member.status == enums.ChatMemberStatus.ADMINISTRATOR
        and getattr(member.privileges, name, None) is True
    )


async def can_set_preset(client, chat_id, user_id):
    if (
        type(chat_id) is not int
        or chat_id >= 0
        or type(user_id) is not int
        or user_id <= 0
        or user_id == 1087968824
    ):
        return False
    member = await client.get_chat_member(chat_id, user_id)
    if member.user is None or member.user.id != user_id or member.user.is_bot:
        return False
    return has_right(member, "can_promote_members")


async def _members(client, chat_id, actor_id, target_id):
    actor = await client.get_chat_member(chat_id, actor_id)
    if actor.user is None or actor.user.id != actor_id or actor.user.is_bot:
        raise TitleDenied("denied")
    target = await client.get_chat_member(chat_id, target_id)
    me = client.me or await client.get_me()
    bot = await client.get_chat_member(chat_id, me.id)
    if bot.status == enums.ChatMemberStatus.ADMINISTRATOR and bot.privileges is None:
        from waku.plugins.agent.tools.moderation import _basic_bot_privileges

        await _basic_bot_privileges(client, chat_id, bot)
    if target_id == me.id or target.user is None or target.user.id != target_id:
        raise TitleDenied("denied")
    if target.status == enums.ChatMemberStatus.OWNER:
        raise TitleDenied("denied")
    if target.status == enums.ChatMemberStatus.ADMINISTRATOR:
        if target.can_be_edited is not True or not has_right(
            bot, "can_promote_members"
        ):
            raise TitleDenied("denied")
        if (
            actor_id == target_id
            and actor.status == enums.ChatMemberStatus.ADMINISTRATOR
        ):
            return target
        if not has_right(actor, "can_promote_members"):
            raise TitleDenied("denied")
        if actor.status != enums.ChatMemberStatus.OWNER:
            current, seen = target, set()
            for _ in range(32):
                promoter = getattr(current, "promoted_by", None)
                if promoter is None or promoter.id in seen:
                    raise TitleDenied("denied")
                if promoter.id == actor_id:
                    break
                seen.add(promoter.id)
                current = await client.get_chat_member(chat_id, promoter.id)
                if current.status != enums.ChatMemberStatus.ADMINISTRATOR:
                    raise TitleDenied("denied")
            else:
                raise TitleDenied("denied")
    elif target.status in (
        enums.ChatMemberStatus.MEMBER,
        enums.ChatMemberStatus.RESTRICTED,
    ):
        if (
            target.status == enums.ChatMemberStatus.RESTRICTED
            and target.is_member is False
        ):
            raise TitleDenied("denied")
        if not has_right(bot, "can_manage_tags"):
            raise TitleDenied("denied")
        if not has_right(actor, "can_manage_tags"):
            if actor_id != target_id:
                raise TitleDenied("denied")
            # None on MEMBER is not proof: Telegram default restrictions still apply.
            from waku.plugins.agent.tools.moderation_permissions import default_rights

            defaults = await default_rights(client, chat_id)
            if defaults.get("edit_rank") is not False or (
                actor.permissions is not None
                and actor.permissions.can_edit_tag is not True
            ):
                raise TitleDenied("denied")
    else:
        raise TitleDenied("denied")
    return target


def validate_title(title):
    if (
        not isinstance(title, str)
        or len(title) > 16
        or any(
            unicodedata.category(c).startswith("C")
            or unicodedata.category(c) == "So"
            or ord(c) >= 0x1F000
            or 0x2600 <= ord(c) <= 0x27FF
            or c in "\ufe0f\u20e3"
            for c in title
        )
    ):
        raise TitleDenied("invalid")


class _TitleClient:
    def __init__(self, client, chat_id, actor_id, target_id):
        self.client, self.chat_id, self.actor_id, self.target_id = (
            client,
            chat_id,
            actor_id,
            target_id,
        )

    def __getattr__(self, name):
        return getattr(self.client, name)

    async def invoke(self, query, **kwargs):
        if isinstance(query, raw.functions.channels.GetParticipant):
            return await self.client.invoke(query, **kwargs)
        target = await _members(
            self.client, self.chat_id, self.actor_id, self.target_id
        )
        if isinstance(query, raw.functions.channels.EditAdmin):
            if target.status != enums.ChatMemberStatus.ADMINISTRATOR:
                raise TitleDenied("denied")
            fresh = (
                await self.client.invoke(
                    raw.functions.channels.GetParticipant(
                        channel=query.channel, participant=query.user_id
                    )
                )
            ).participant
            if (
                not isinstance(fresh, raw.types.ChannelParticipantAdmin)
                or fresh.can_edit is not True
                or target.promoted_by is None
                or fresh.promoted_by != target.promoted_by.id
            ):
                raise TitleDenied("denied")
            if any(
                bool(getattr(fresh.admin_rights, flag))
                != bool(getattr(query.admin_rights, flag))
                for flag in inspect.signature(raw.types.ChatAdminRights).parameters
            ):
                raise TitleDenied("changed")
        elif (
            not isinstance(query, raw.functions.messages.EditChatParticipantRank)
            or target.status == enums.ChatMemberStatus.ADMINISTRATOR
        ):
            raise TitleDenied("denied")
        result = await self.client.invoke(query, retries=1, sleep_threshold=0)
        if result is None or result is False:
            raise TitleDenied("error")
        return result


async def change_title(client, chat_id, actor_id, target_id, title):
    if (
        type(chat_id) is not int
        or chat_id >= 0
        or type(target_id) is not int
        or target_id <= 0
        or type(actor_id) is not int
        or actor_id <= 0
        or actor_id == 1087968824
    ):
        raise TitleDenied("denied")
    validate_title(title)
    target = await _members(client, chat_id, actor_id, target_id)
    facade = _TitleClient(client, chat_id, actor_id, target_id)
    if target.status == enums.ChatMemberStatus.ADMINISTRATOR:
        peer = await client.resolve_peer(chat_id)
        if not isinstance(peer, raw.types.InputPeerChannel):
            raise TitleDenied("unsupported")
        return await Client.set_administrator_title(facade, chat_id, target_id, title)
    return await Client.set_chat_member_tag(facade, chat_id, target_id, title)
