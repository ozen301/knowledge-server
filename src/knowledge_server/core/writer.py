"""Commit unreviewed note proposals and push branches with ref leases."""

import hashlib
import secrets
from collections.abc import Iterable

from knowledge_server.core.git import PROPOSAL_LOCK, ProposalBranches
from knowledge_server.core.limits import DEFAULT_LIMITS, Limits
from knowledge_server.core.models import (
    DomainErrorCode,
    KnowledgeError,
    ProposalResult,
    ProposeEditRequest,
    ProposeNoteRequest,
    edit_error,
    validate_proposal,
    write_text_bytes,
)
from knowledge_server.core.paths import TargetKind

_UTF8_BOM = b"\xef\xbb\xbf"


def propose_note(
    branches: ProposalBranches,
    request: ProposeNoteRequest,
    limits: Limits = DEFAULT_LIMITS,
) -> ProposalResult:
    """Propose a new note absent from the selected commit.

    Returns:
        Branch name, root-relative path, and hash after a successful push.

    Raises:
        KnowledgeError: `ALREADY_EXISTS` for an existing entry or branch;
            request, path, size, or Git operation errors.
    """
    _check_limits([request.content], 0, limits)
    branches.policy.parse(request.path, TargetKind.FILE)
    proposal = _branch(request.topic, request.proposal)
    data = request.content.encode()
    with PROPOSAL_LOCK:
        base = branches.base(request.proposal)
        if branches.entry(base, request.path, absent_ok=True) is not None:
            raise KnowledgeError(DomainErrorCode.ALREADY_EXISTS)
        _check_size(data, limits)
        branches.commit_and_push(
            base,
            proposal,
            request.path,
            data,
            revision=request.proposal is not None,
            summary=request.summary,
        )
    return _result(proposal, request.path, data)


def propose_edit(
    branches: ProposalBranches,
    request: ProposeEditRequest,
    limits: Limits = DEFAULT_LIMITS,
) -> ProposalResult:
    """Apply exact replacements to committed bytes and push an unreviewed commit.

    Preserve BOM and CRLF, reject mixed line endings, and check the raw-byte
    hash before applying edits in order. A failing edit publishes no commit.

    Returns:
        Branch name, root-relative path, and hash after a successful push.

    Raises:
        KnowledgeError: `STALE_CONTENT`, `MIXED_LINE_ENDINGS`, `TEXT_NOT_FOUND`,
            `TEXT_NOT_UNIQUE`, or request, path, loading, size, and Git errors.
    """
    texts = [text for edit in request.edits for text in (edit.old, edit.new)]
    _check_limits(texts, len(request.edits), limits)
    branches.policy.parse(request.path, TargetKind.FILE)
    proposal = _branch(request.topic, request.proposal)
    with PROPOSAL_LOCK:
        base = branches.base(request.proposal)
        raw, text = branches.load(base, request.path, limits)
        if hashlib.sha256(raw).hexdigest() != request.base_sha256:
            raise KnowledgeError(DomainErrorCode.STALE_CONTENT)
        crlf = b"\r\n" in raw
        if crlf and raw.count(b"\n") != raw.count(b"\r\n"):
            raise KnowledgeError(DomainErrorCode.MIXED_LINE_ENDINGS)
        for position, edit in enumerate(request.edits, start=1):
            start = text.find(edit.old)
            if start < 0:
                raise edit_error(DomainErrorCode.TEXT_NOT_FOUND, position)
            if text.find(edit.old, start + 1) >= 0:
                raise edit_error(DomainErrorCode.TEXT_NOT_UNIQUE, position)
            text = text[:start] + edit.new + text[start + len(edit.old) :]
        if crlf:
            text = text.replace("\n", "\r\n")
        data = (_UTF8_BOM if raw.startswith(_UTF8_BOM) else b"") + text.encode()
        _check_size(data, limits)
        branches.commit_and_push(
            base,
            proposal,
            request.path,
            data,
            revision=request.proposal is not None,
            summary=request.summary,
        )
    return _result(proposal, request.path, data)


def _branch(topic: str | None, proposal: str | None) -> str:
    if proposal is None:
        proposal = f"proposal/{topic}-{secrets.token_hex(2)}"
    validate_proposal(proposal)
    return proposal


def _check_limits(texts: Iterable[str], edits: int, limits: Limits) -> None:
    if (
        write_text_bytes(texts) > limits.max_write_text_bytes
        or edits > limits.max_edits
    ):
        raise KnowledgeError(DomainErrorCode.INVALID_ARGUMENT)


def _check_size(data: bytes, limits: Limits) -> None:
    if len(data) > limits.max_file_bytes:
        raise KnowledgeError(DomainErrorCode.FILE_TOO_LARGE)


def _result(proposal: str, path: str, data: bytes) -> ProposalResult:
    return ProposalResult(
        proposal=proposal, path=path, content_sha256=hashlib.sha256(data).hexdigest()
    )
