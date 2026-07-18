#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from devin.ai.hybrid_memory_client import LocalMemoryStore
from devin.memory.taxonomy import build_memory_tags, tag_value

SEED_MEMORIES = [

    {
        'key': 'steam_wrong_sources_scaffold_failure_v1',
        'content': 'Verified failed scaffold for Steam Profile Checker. Intended task: build a Steam profile risk-assessment MVP using only official Steam Web API documentation, secure OS keyring, non-blocking GUI, simulated HTTP tests, and separate risk_score/confidence/data_completeness. Observed failure: generated sources were GitHub, OpenWeatherMap and NewsAPI instead of Steam; HTTP client returned simulated data rather than real requests; API key was stored plaintext in config/settings.json; generated tests failed. Retry rule: inject verified web evidence before planning, preserve unknowns instead of inventing endpoints, run tests, and commit only when the quality gate passes.',
        'importance': 0.95,
        'tags': build_memory_tags(kind='failure_lesson', status='verified_failure', polarity='negative', evidence='tests_3_passed_3_failed_and_code_review', project='Steam Profile Checker', failure_type='wrong_sources_scaffold', memory_key='steam_wrong_sources_scaffold_failure_v1') + ['topic:steam', 'topic:scaffold', 'topic:official_sources'],
    },
    {
        'key': 'devin_chat_only_output_failure_v1',
        'content': 'Verified failure pattern: if the user asks DEVIN to create/build an app and DEVIN only replies with Markdown code fences or snippets in chat, the task is not complete. Expected behavior: write real files in the project, run the relevant tests, report failures honestly, and commit only after the quality gate passes.',
        'importance': 0.95,
        'tags': build_memory_tags(kind='failure_lesson', status='verified_failure', polarity='negative', evidence='human_review', project='Steam Profile Checker', failure_type='chat_only_output', memory_key='devin_chat_only_output_failure_v1') + ['topic:scaffold', 'topic:eval'],
    },
    {
        'key': 'user_memory_policy_v1',
        'content': 'Human preference: build a complete memory, but avoid contamination. Save successful lessons as successes and mistakes as mistakes. Errors are valuable only when labeled as failures with cause, evidence, and a safer retry rule. Hypotheses and raw observations must not be promoted into shared recall until reviewed or eval-verified.',
        'importance': 1.0,
        'tags': build_memory_tags(kind='user_preference', status='human_confirmed', polarity='positive', evidence='explicit_user_preference', memory_key='user_memory_policy_v1') + ['topic:memory', 'topic:anti_contamination'],
    },
    {
        'key': 'rig_shared_memory_architecture_v1',
        'content': 'Rig architecture context: DEVIN, TEACHER and HERMES are intended to run as separate local agents/models, each with its own local memory, plus a shared fourth-disk memory for promoted knowledge. Shared memory should prefer human-confirmed facts, verified successes, verified failures, and eval results; agent-private raw notes stay local until librarian/teacher review.',
        'importance': 0.9,
        'tags': build_memory_tags(kind='project_context', status='human_confirmed', polarity='positive', evidence='explicit_user_architecture', memory_key='rig_shared_memory_architecture_v1', share_scope='multi_agent_shared_candidate') + ['topic:rig', 'topic:shared_memory', 'bot:devin', 'bot:teacher', 'bot:hermes'],
    },
]


def _existing_memory_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.exists():
        return keys
    with path.open('r', encoding='utf-8') as fh:
        for line in fh:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = record.get('memory_key') or tag_value(record.get('tags'), 'memory_key', '')
            if key:
                keys.add(key)
    return keys


def seed_core_memory(config: dict | None = None) -> dict:
    store = LocalMemoryStore(config or {})
    existing = _existing_memory_keys(store.path)
    stored: list[str] = []
    skipped: list[str] = []
    for memory in SEED_MEMORIES:
        if memory['key'] in existing:
            skipped.append(memory['key'])
            continue
        result = store.store(memory['content'], tags=memory['tags'], importance=memory['importance'])
        if result == 'local_stored':
            stored.append(memory['key'])
        else:
            skipped.append(memory['key'])
    return {'path': str(store.path), 'stored': stored, 'skipped': skipped}


if __name__ == '__main__':
    print(json.dumps(seed_core_memory(), ensure_ascii=False, indent=2, sort_keys=True))
