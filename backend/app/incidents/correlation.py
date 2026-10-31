"""Bounded, deterministic lexical correlation for likely shared symptoms."""

import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from itertools import combinations
from uuid import UUID

from app.config import Settings

DETECTOR_VERSION = "lexical-v1"
STOPWORDS = frozenset(
    "a an and are as at be been being but by can could did do does doing for from had has "
    "have he her here hers him his how i if in into is it its me my of on or our ours she "
    "so than that the their them then there these they this those to too us very was we were "
    "what when where which who why will with would you your yours please support help "
    "error errors problem problems issue issues bug bugs failed failure failing broken "
    "not cannot unable working works system app application service something thing "
    "error problema problemas ayuda fallo error Fehler problem probleme aide panne "
    "ошибка ошибки проблема проблемы помощь сбой работает работают".split()
)
TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class TicketText:
    id: UUID
    organization_id: UUID
    requester_contact_id: UUID
    title: str
    body: str
    status: str
    priority: str
    category_id: UUID | None
    category_name: str | None
    requester_name: str
    created_at: datetime

    @property
    def title_terms(self) -> frozenset[str]:
        return significant_terms(self.title)

    @property
    def terms(self) -> frozenset[str]:
        return significant_terms(f"{self.title} {self.body}")


@dataclass(frozen=True)
class CandidateGroup:
    ticket_ids: tuple[UUID, ...]
    group_key: str
    input_fingerprint: str
    title: str
    summary: str
    score: float
    evidence: dict


@dataclass(frozen=True)
class PairSignal:
    score: float
    shared_terms: tuple[str, ...]
    category_match: bool
    minutes_apart: int


def significant_terms(text: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return frozenset(
        word for word in TOKEN_RE.findall(normalized) if len(word) >= 3 and word not in STOPWORDS
    )


def pair_signal(left: TicketText, right: TicketText, settings: Settings) -> PairSignal | None:
    delta_seconds = abs((left.created_at - right.created_at).total_seconds())
    window_seconds = settings.radar_pair_window_minutes * 60
    if delta_seconds > window_seconds:
        return None
    shared = tuple(sorted(left.terms & right.terms))
    # A category match or a single broad term is never enough to create a proposal.
    if len(shared) < settings.radar_min_shared_terms:
        return None
    union = left.terms | right.terms
    lexical = len(shared) / len(union) if union else 0.0
    category_match = left.category_id is not None and left.category_id == right.category_id
    title_shared = len(left.title_terms & right.title_terms)
    if not (
        lexical >= settings.radar_similarity_threshold or (category_match and title_shared >= 2)
    ):
        return None
    temporal = max(0.0, 1 - delta_seconds / window_seconds)
    score = min(1.0, 0.65 * lexical + 0.20 * category_match + 0.15 * temporal)
    return PairSignal(
        score=round(score, 4),
        shared_terms=shared,
        category_match=category_match,
        minutes_apart=int(delta_seconds // 60),
    )


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def candidate_groups(tickets: list[TicketText], settings: Settings) -> list[CandidateGroup]:
    """Build complete-link groups from a bounded recent ticket window.

    Inverted token postings prune comparisons. Very frequent terms are ignored; groups
    are capped at eight tickets and require at least two distinct contacts.
    """
    tickets = sorted(tickets, key=lambda item: (item.created_at, str(item.id)))
    if len(tickets) < 2:
        return []

    postings: dict[str, list[int]] = defaultdict(list)
    for index, ticket in enumerate(tickets):
        terms = ticket.terms
        for term in terms:
            postings[term].append(index)

    max_document_frequency = max(3, int(len(tickets) * 0.25))
    pair_indices: set[tuple[int, int]] = set()
    exhausted = False
    for term in sorted(postings):
        posting_indexes = postings[term]
        if len(posting_indexes) > max_document_frequency:
            continue
        for pair in combinations(posting_indexes, 2):
            pair_indices.add(pair)
            if len(pair_indices) >= settings.radar_max_candidate_pairs:
                exhausted = True
                break
        if exhausted:
            break

    edges: dict[tuple[int, int], PairSignal] = {}
    neighbors: dict[int, set[int]] = defaultdict(set)
    for left_index, right_index in sorted(pair_indices):
        signal = pair_signal(tickets[left_index], tickets[right_index], settings)
        if signal is not None:
            edges[(left_index, right_index)] = signal
            neighbors[left_index].add(right_index)
            neighbors[right_index].add(left_index)

    groups: dict[tuple[int, ...], CandidateGroup] = {}
    for left_index, right_index in sorted(edges):
        group_indexes = [left_index, right_index]
        possible = sorted(
            neighbors[left_index] & neighbors[right_index],
            key=lambda idx: (tickets[idx].created_at, str(tickets[idx].id)),
        )
        for candidate_index in possible:
            if len(group_indexes) >= 8:
                break
            span = (
                tickets[max(group_indexes + [candidate_index])].created_at
                - tickets[min(group_indexes + [candidate_index])].created_at
            ).total_seconds()
            if span > settings.radar_pair_window_minutes * 60:
                continue
            if all(
                (min(candidate_index, member), max(candidate_index, member)) in edges
                for member in group_indexes
            ):
                group_indexes.append(candidate_index)
        group_indices = tuple(sorted(group_indexes))
        if (
            len(group_indices) < 2
            or len({tickets[idx].requester_contact_id for idx in group_indices}) < 2
        ):
            continue
        group_tickets = [tickets[idx] for idx in group_indices]
        if not _shares_a_qualifying_signal(group_tickets, edges, group_indices, settings):
            continue
        candidate_group = _make_group(group_tickets, edges, group_indices, settings)
        groups[group_indices] = candidate_group

    candidates = sorted(
        groups.values(), key=lambda group: (-len(group.ticket_ids), group.group_key)
    )
    # Keep only maximal groups where the smaller group contributes no distinct evidence.
    maximal: list[CandidateGroup] = []
    for proposal_group in candidates:
        ids = set(proposal_group.ticket_ids)
        if any(ids < set(previous.ticket_ids) for previous in maximal):
            continue
        maximal.append(proposal_group)
    return maximal[:100]


def _shares_a_qualifying_signal(
    tickets: list[TicketText],
    edges: dict[tuple[int, int], PairSignal],
    indexes: tuple[int, ...],
    settings: Settings,
) -> bool:
    # At least one significant term must appear in half the group, and every pair must
    # already have passed the lexical threshold. This keeps category-only groups out.
    counts = Counter(term for ticket in tickets for term in ticket.terms)
    threshold = max(2, (len(tickets) + 1) // 2)
    shared = any(count >= threshold for count in counts.values())
    complete = all(pair in edges for pair in combinations(indexes, 2))
    return shared and complete


def _make_group(
    tickets: list[TicketText],
    edges: dict[tuple[int, int], PairSignal],
    indexes: tuple[int, ...],
    settings: Settings,
) -> CandidateGroup:
    term_counts = Counter(term for ticket in tickets for term in ticket.terms)
    min_count = max(2, (len(tickets) + 1) // 2)
    terms = sorted(term for term, count in term_counts.items() if count >= min_count)[:8]
    category_counts: Counter[str] = Counter(
        ticket.category_name for ticket in tickets if ticket.category_name is not None
    )
    category_matches: list[dict[str, str | int]] = [
        {"name": name, "ticket_count": count}
        for name, count in sorted(category_counts.items())
        if count >= 2
    ]
    pairs: list[tuple[int, int]] = list(combinations(indexes, 2))
    score = round(sum(edges[pair].score for pair in pairs) / len(pairs), 4)
    times = [ticket.created_at for ticket in tickets]
    span_minutes = int((max(times) - min(times)).total_seconds() // 60)
    group_ids = tuple(sorted((ticket.id for ticket in tickets), key=str))
    group_key = _digest([str(ticket_id) for ticket_id in group_ids])
    inputs = [
        {
            "id": str(ticket.id),
            "terms": sorted(ticket.terms),
            "category_id": str(ticket.category_id) if ticket.category_id else None,
            "created_at": ticket.created_at.isoformat(),
        }
        for ticket in sorted(tickets, key=lambda value: str(value.id))
    ]
    input_fingerprint = _digest(inputs)
    distinct_contacts = len({ticket.requester_contact_id for ticket in tickets})
    ticket_by_index = dict(zip(indexes, tickets, strict=True))
    evidence: dict[str, object] = {
        "detector_version": DETECTOR_VERSION,
        "rule": "complete-link significant-term overlap with distinct requesters",
        "threshold": settings.radar_similarity_threshold,
        "minimum_shared_terms": settings.radar_min_shared_terms,
        "shared_terms": terms,
        "category_matches": category_matches,
        "ticket_count": len(tickets),
        "distinct_contact_count": distinct_contacts,
        "window_minutes": settings.radar_pair_window_minutes,
        "observed_span_minutes": span_minutes,
        "ticket_refs": [str(ticket_id) for ticket_id in group_ids],
        "pair_signals": [
            {
                "ticket_ids": [str(ticket_by_index[index].id) for index in pair],
                "shared_terms": list(edges[pair].shared_terms),
                "category_match": edges[pair].category_match,
                "minutes_apart": edges[pair].minutes_apart,
                "score": edges[pair].score,
            }
            for pair in pairs
        ],
        "interpretation": (
            "Candidate symptom correlation only; it does not establish a common root cause."
        ),
    }
    label = ", ".join(terms[:3]) or "repeated symptoms"
    title = f"Possible recurring issue: {label}"[:200]
    category_text = ", ".join(str(item["name"]) for item in category_matches)
    evidence_text = f"share terms {', '.join(terms)}" if terms else "show repeated lexical signals"
    if category_text:
        evidence_text += f" and category {category_text}"
    summary = (
        f"{len(tickets)} tickets from {distinct_contacts} contacts were created "
        f"within {span_minutes} minutes; they {evidence_text}. This is a review suggestion, "
        "not a root-cause finding."
    )[:500]
    return CandidateGroup(group_ids, group_key, input_fingerprint, title, summary, score, evidence)
