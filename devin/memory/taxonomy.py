from __future__ import annotations

from typing import Iterable

MEMORY_SCHEMA_VERSION = 'memory.v1'
MEMORY_STATUSES = {
    'pending_review', 'verified_success', 'verified_failure', 'human_confirmed',
    'hypothesis', 'inconclusive', 'revoked', 'superseded', 'quarantine', 'syntax_only',
}
RECALLABLE_MEMORY_STATUSES = {'verified_success', 'verified_failure', 'human_confirmed'}
EXCLUDED_RECALL_STATUSES = MEMORY_STATUSES - RECALLABLE_MEMORY_STATUSES
MEMORY_KINDS = {
    'lesson', 'failure_lesson', 'success_pattern', 'correction', 'user_preference',
    'project_context', 'hypothesis', 'eval_result', 'raw_observation',
}
MEMORY_VISIBILITIES = {'local', 'shared', 'agent_private'}
PROMOTION_POLICIES = {'manual_required', 'never', 'eligible_after_eval', 'promoted'}


def tag_value(tags: Iterable[str] | None, prefix: str, default: str = '') -> str:
    return next(
        (tag.split(':', 1)[1] for tag in (tags or [])
         if isinstance(tag, str) and tag.startswith(prefix + ':')),
        default,
    )


def is_recallable_status(status: str) -> bool:
    return status in RECALLABLE_MEMORY_STATUSES


def build_memory_tags(
    *,
    agent: str = 'devin',
    project: str | None = None,
    domain: str = 'software-engineering',
    kind: str = 'lesson',
    status: str = 'pending_review',
    polarity: str = 'neutral',
    evidence: str = 'unspecified',
    visibility: str = 'local',
    promotion: str = 'manual_required',
    agent_scope: str = 'devin',
    share_scope: str = 'agent_local',
    failure_type: str | None = None,
    memory_key: str | None = None,
) -> list[str]:
    tags = [
        agent,
        f'domain:{domain}',
        f'kind:{kind}',
        f'status:{status}',
        f'polarity:{polarity}',
        f'evidence:{evidence}',
        f'visibility:{visibility}',
        f'promotion:{promotion}',
        f'agent_scope:{agent_scope}',
        f'share_scope:{share_scope}',
    ]
    if project:
        tags.append(f'project:{project}')
    if failure_type:
        tags.append(f'failure_type:{failure_type}')
    if memory_key:
        tags.append(f'memory_key:{memory_key}')
    return tags


def validate_memory_tags(tags: Iterable[str] | None) -> list[str]:
    tags = list(tags or [])
    status = tag_value(tags, 'status', 'pending_review')
    kind = tag_value(tags, 'kind', 'lesson')
    visibility = tag_value(tags, 'visibility', 'local')
    promotion = tag_value(tags, 'promotion', 'manual_required')
    errors: list[str] = []
    if status not in MEMORY_STATUSES:
        errors.append(f'unknown status:{status}')
    if kind not in MEMORY_KINDS:
        errors.append(f'unknown kind:{kind}')
    if visibility not in MEMORY_VISIBILITIES:
        errors.append(f'unknown visibility:{visibility}')
    if promotion not in PROMOTION_POLICIES:
        errors.append(f'unknown promotion:{promotion}')
    if status in EXCLUDED_RECALL_STATUSES and promotion == 'promoted':
        errors.append('non-recallable status cannot be promotion:promoted')
    return errors
