import asyncio
import random
from weakref import WeakValueDictionary

from pydantic_ai import Agent, ModelRetry

from waku import affection
from waku.common.memory_store import memttlcache
from waku.config import app_config
from waku.logger import logger
from waku.plugins.agent import datatype, quota, state, trace
from waku.plugins.agent.localization import tr

_user_memory_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()
_user_memory_locks_lock = asyncio.Lock()


async def _get_user_memory_lock(user_id: int) -> asyncio.Lock:
    async with _user_memory_locks_lock:
        lock = _user_memory_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            _user_memory_locks[user_id] = lock
        return lock


async def update_user_memory(
    agent: Agent[None, datatype.UserMemoryResult],
    message_text: str,
    user_id: int,
    subject: quota.Subject,
):
    if not await quota.can_start(subject):
        # Silently skip memory updates without quota; this background work should not disturb users.
        logger.debug(f"Skip updating memory for user {user_id}: no quota")
        return
    lock = await _get_user_memory_lock(user_id)
    async with lock:
        # Prevent spammers from repeatedly triggering memory updates.
        throttle_key = f"user_memory_update_throttle:{user_id}"
        if await memttlcache.get(throttle_key):
            logger.debug(
                f"Skip updating memory for user {user_id} due to 30s rate limit"
            )
            return
        await memttlcache.set(throttle_key, True, ttl=300)

        logger.debug(f"Updating memory for user {user_id}")
        old_memory = await memttlcache.get(state.memory_key(user_id))
        if old_memory and isinstance(old_memory, datatype.ChatMemoryy):
            message_text = tr("update_user_memory", p0=old_memory, p1=message_text)

        # Enforce a model-call timeout to avoid stalling the event loop.
        session = await trace.start_trace("memory", user_id=user_id)
        try:
            timeout = app_config.agent_model_timeout
            coro = agent.run(
                output_type=datatype.UserMemoryResult,
                user_prompt=(tr("summarize_user_memory", p0=message_text)),
            )

            if timeout > 0:
                try:
                    memory_result = await asyncio.wait_for(coro, timeout=timeout)
                except TimeoutError as e:
                    trace.mark_trace(session, status="timeout", error=e)
                    logger.warning(f"update_user_memory timed out for user {user_id}")
                    return  # Return silently after timeout without interrupting the main flow.
            else:
                memory_result = await coro

            # Bill this model call against the triggering message; the memory it produces
            # would otherwise incur an invisible cost.
            await quota.settle(subject, memory_result.usage)
            # Account for this model call only; later memory merges and affection updates are separate.
            trace.mark_trace(
                session, usage=memory_result.usage, output=str(memory_result.output)
            )
        except Exception as e:
            trace.mark_trace(session, status="error", error=e)
            raise
        finally:
            trace.finish_trace(session)

        logger.debug(f"Agent memory history: {memory_result.output}")
        result = memory_result.output
        try:
            affection_change = result.get_affection_change()
            affection_change += random.randint(-4, 4)
        except ValueError:
            raise ModelRetry(
                "Invalid affection change value from agent, please provide 'affection_option' and 'affection_change_amplitude' fields correctly."
                "The 'affection_option' should be one of 'increase', 'decrease', or 'no_change'."
                "The 'affection_change_amplitude' should be one of 'small', 'medium', or 'large'."
            )
        try:
            if affection_change != 0:
                await affection.update_user_affection(user_id, affection_change)
        except Exception as e:
            logger.exception(f"Error updating user affection: {e}")
        new_memory = result.get_memory()
        if old_memory:
            # Merge memory lists, deduplicating each field and limiting it to three entries.
            for field in datatype.ChatMemoryy.model_fields:
                old_value = getattr(old_memory, field, [])
                new_value = getattr(new_memory, field, [])
                if old_value and new_value:
                    if isinstance(old_value, list) and isinstance(new_value, list):
                        combined = list(dict.fromkeys(old_value + new_value))
                        setattr(new_memory, field, combined[:3])
                    elif isinstance(old_value, str) and isinstance(new_value, str):
                        if new_value not in old_value:
                            combined = [old_value, new_value]
                        else:
                            combined = [old_value]
                        setattr(new_memory, field, combined)
                    elif isinstance(old_value, list) and isinstance(new_value, str):
                        if new_value not in old_value:
                            combined = old_value + [new_value]
                        else:
                            combined = old_value
                        setattr(new_memory, field, combined[:3])
                    elif isinstance(old_value, str) and isinstance(new_value, list):
                        if old_value not in new_value:
                            combined = [old_value] + new_value
                        else:
                            combined = new_value
                        setattr(new_memory, field, combined[:3])
                elif old_value and not new_value:
                    if isinstance(old_value, list):
                        setattr(new_memory, field, old_value[:3])
                    else:
                        setattr(new_memory, field, [old_value])
        await memttlcache.set(
            state.memory_key(user_id),
            new_memory,
            ttl=86400 * 30,  # 30 days
        )
