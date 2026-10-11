"""Shared quota snapshots for profile and administrator pages."""

from waku import database
from waku.config import app_config
from waku.webapp.schemas import AgentUsageOut


async def usage_out(scope: str, scope_id: int, exempt: bool = False) -> AgentUsageOut:
    requests, free_used, input_tokens, output_tokens = await database.get_usage(
        scope, scope_id, database.quota_day()
    )
    if scope == database.SCOPE_USER:
        limit = app_config.agent_quota_free_daily_tokens
        free_limit = None if limit <= 0 else limit
    else:
        policy = await database.get_chat_policy(scope_id)
        free_limit = database.agent_free_daily_tokens(policy)
        exempt = exempt or policy.agent_quota_exempt
    return AgentUsageOut(
        scope=scope,
        scope_id=scope_id,
        requests_today=requests,
        free_used_tokens_today=free_used,
        free_limit_tokens=free_limit,
        credits=await database.get_credit(scope, scope_id),
        input_tokens_today=input_tokens,
        output_tokens_today=output_tokens,
        exempt=exempt,
    )
