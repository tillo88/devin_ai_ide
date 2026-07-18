"""Structured contracts for DEVIN LLM outputs.

These Pydantic models are intentionally dependency-light: they work without
Instructor installed, but can be passed directly as Instructor response models
when we enable schema-first extraction/retry flows.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

ReviewStatus = Literal[
    "verified_success",
    "human_confirmed",
    "verified_failure",
    "failed",
    "needs_correction",
    "runner_error",
    "pending_review",
]


class MethodTrace(BaseModel):
    hypothesis: str = Field(default="", description="Initial operational assumption or plan.")
    checks_run: List[str] = Field(default_factory=list, description="Concrete tests, commands, validators, or inspections performed.")
    evidence: List[str] = Field(default_factory=list, description="Observed facts that support the review decision.")
    failure_mode: str = Field(default="", description="Specific failure pattern, if any.")
    next_action: str = Field(default="", description="Recommended next operational step.")

    def compact(self) -> str:
        parts = []
        if self.hypothesis:
            parts.append(f"hypothesis: {self.hypothesis}")
        if self.checks_run:
            parts.append("checks: " + "; ".join(self.checks_run))
        if self.evidence:
            parts.append("evidence: " + "; ".join(self.evidence))
        if self.failure_mode:
            parts.append(f"failure_mode: {self.failure_mode}")
        if self.next_action:
            parts.append(f"next_action: {self.next_action}")
        return " | ".join(parts)


class TrainingReviewDecision(BaseModel):
    attempt_id: str
    status: ReviewStatus
    rationale: str = Field(default="", description="Short human/teacher-readable reason for the decision.")
    method: MethodTrace = Field(default_factory=MethodTrace)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    lesson_candidate: str = Field(default="", description="Possible memory lesson; not auto-promoted.")
    tags: List[str] = Field(default_factory=list)
    evidence: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("attempt_id")
    @classmethod
    def attempt_id_required(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("attempt_id is required")
        return value

    def to_store_payload(self, reviewer: str = "teacher") -> Dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "status": self.status,
            "rationale": self.rationale,
            "method_trace": self.method.compact(),
            "failure_mode": self.method.failure_mode,
            "next_action": self.method.next_action,
            "lesson_candidate": self.lesson_candidate,
            "reviewer": reviewer,
            "confidence": self.confidence,
            "tags": self.tags,
            "evidence": self.evidence,
        }


class LessonCandidate(BaseModel):
    content: str
    status: Literal["pending_review", "verified_success", "verified_failure"] = "pending_review"
    tags: List[str] = Field(default_factory=list)
    evidence: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("content")
    @classmethod
    def content_required(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("lesson content is required")
        return value


class CrawlKnowledgeRecord(BaseModel):
    url: str
    title: str = ""
    markdown: str
    source: Literal["crawl4ai", "basic_fetch", "mock"] = "basic_fetch"
    links: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("markdown")
    @classmethod
    def markdown_required(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("markdown/text content is required")
        return value

    def as_knowledge_markdown(self) -> str:
        title = self.title or self.url
        links = "\n".join(f"- {link}" for link in self.links[:50])
        meta = "\n".join(f"- {k}: {v}" for k, v in sorted(self.metadata.items()))
        return (
            f"# Fonte: {title}\n\n"
            f"url: {self.url}\n"
            f"source: {self.source}\n\n"
            f"## Metadata\n{meta or '- none'}\n\n"
            f"## Links\n{links or '- none'}\n\n"
            f"## Content\n\n{self.markdown.strip()}\n"
        )
