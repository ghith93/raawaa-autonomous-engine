"""Channel-agnostic publishing queue.

Why a queue rather than direct posting: every channel the founder actually has
(Instagram, TikTok, a storefront, a WhatsApp broadcast) either has no publishing
API available to a Syrian account or silently rate-limits one. A local queue with
an explicit state per item means a failed post is a retryable row, not a lost
day, and the same content can be drained to whichever channel is reachable.

No network calls happen here. Adapters are injected, so the queue is testable
and the policy gate (human approval before anything leaves the machine) lives in
one place.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

QUEUE_STATES = ("draft", "approved", "scheduled", "published", "failed", "withdrawn")


class PublishError(ValueError):
    pass


class ChannelUnavailable(PublishError):
    """The channel cannot be reached from here (missing adapter, blocked API)."""


def now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Post:
    post_id: str
    caption: str
    media: list[str]
    channels: list[str]
    state: str = "draft"
    scheduled_for: str | None = None
    published_at: str | None = None
    failures: list[dict] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def content_hash(self) -> str:
        payload = self.caption + "|" + "|".join(sorted(self.media))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def __post_init__(self) -> None:
        if self.state not in QUEUE_STATES:
            raise PublishError(f"unknown state: {self.state}")
        if not self.caption.strip():
            raise PublishError("a post needs a caption")
        if not self.channels:
            raise PublishError("a post needs at least one channel")


class Channel(Protocol):
    name: str

    def available(self) -> tuple[bool, str]: ...
    def publish(self, post: Post) -> str: ...   # returns a permalink or id


class InstagramGraphChannel:
    """Instagram Content Publishing, behind an explicit token and availability gate.

    Deliberately reports itself unavailable rather than pretending: the API needs a
    business account and a publicly reachable media URL, neither of which a Syrian
    personal account reliably has. When those exist it works unchanged.
    """

    name = "instagram"

    def __init__(self, env: dict[str, str] | None = None, media_base_url: str | None = None):
        import os
        self.env = dict(os.environ if env is None else env)
        self.media_base_url = media_base_url

    def available(self) -> tuple[bool, str]:
        if not self.env.get("IG_USER_ID", "").strip():
            return False, "missing IG_USER_ID"
        if not self.env.get("IG_ACCESS_TOKEN", "").strip():
            return False, "missing IG_ACCESS_TOKEN"
        if not self.media_base_url:
            return False, "media must be publicly reachable; no base URL configured"
        return True, "credentials and media host present"

    def publish(self, post: "Post") -> str:
        ok, reason = self.available()
        if not ok:
            raise ChannelUnavailable(f"instagram: {reason}")
        return self._create_and_publish(post)

    def _create_and_publish(self, post: "Post") -> str:
        import json as _json
        import urllib.parse
        import urllib.request

        token = self.env["IG_ACCESS_TOKEN"].strip()
        user = self.env["IG_USER_ID"].strip()
        image_url = f"{self.media_base_url.rstrip('/')}/{post.media[0]}"
        create = urllib.request.Request(
            f"https://graph.facebook.com/v21.0/{user}/media?"
            + urllib.parse.urlencode({"image_url": image_url, "caption": post.caption,
                                      "access_token": token}),
            method="POST",
        )
        with urllib.request.urlopen(create, timeout=60) as response:
            creation_id = _json.loads(response.read())["id"]
        publish = urllib.request.Request(
            f"https://graph.facebook.com/v21.0/{user}/media_publish?"
            + urllib.parse.urlencode({"creation_id": creation_id, "access_token": token}),
            method="POST",
        )
        with urllib.request.urlopen(publish, timeout=60) as response:
            return _json.loads(response.read())["id"]


class LocalFileChannel:
    """Writes the post as a folder the founder can upload by hand.

    This is not a workaround, it is the honest fallback: a manual upload is
    always possible, whereas API posting from some regions is not.
    """

    name = "manual"

    def __init__(self, root: str | Path = "raawaa/out/posts"):
        self.root = Path(root)

    def available(self) -> tuple[bool, str]:
        return True, "manual upload always possible"

    def publish(self, post: Post) -> str:
        folder = self.root / post.post_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "caption.txt").write_text(post.caption, encoding="utf-8")
        manifest = {
            "post_id": post.post_id,
            "channels": post.channels,
            "media": post.media,
            "content_hash": post.content_hash(),
            "note": "upload these files by hand, then mark the post published",
        }
        (folder / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return str(folder)


@dataclass
class Queue:
    """JSON-lines backed queue; survives a crash and is diffable."""

    path: Path = Path("data/publish-queue.jsonl")
    channels: dict[str, Channel] = field(default_factory=dict)
    posts: dict[str, Post] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.load()

    # -- persistence --------------------------------------------------------
    def load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            post = Post(**raw)
            self.posts[post.post_id] = post

    def _append(self, post: Post) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(post.__dict__, ensure_ascii=False) + "\n")

    # -- workflow -----------------------------------------------------------
    def add(self, post: Post) -> None:
        if post.post_id in self.posts:
            raise PublishError(f"duplicate post id: {post.post_id}")
        self.posts[post.post_id] = post
        self._append(post)

    def approve(self, post_id: str) -> None:
        post = self._get(post_id)
        if post.state != "draft":
            raise PublishError(f"only drafts can be approved, this is {post.state}")
        post.state = "approved"
        self._append(post)

    def _get(self, post_id: str) -> Post:
        if post_id not in self.posts:
            raise PublishError(f"unknown post: {post_id}")
        return self.posts[post_id]

    def publish(self, post_id: str) -> dict[str, str]:
        """Send an approved post to every channel it names. Human gate enforced."""
        post = self._get(post_id)
        if post.state not in {"approved", "failed"}:
            raise PublishError(
                f"post is {post.state}; only an approved post may be published "
                "(approval is the human gate)"
            )
        results: dict[str, str] = {}
        for channel_name in post.channels:
            channel = self.channels.get(channel_name)
            if channel is None:
                post.failures.append({"channel": channel_name, "at": now().isoformat(),
                                      "reason": "no adapter registered"})
                continue
            ok, reason = channel.available()
            if not ok:
                post.failures.append({"channel": channel_name, "at": now().isoformat(),
                                      "reason": reason})
                continue
            try:
                results[channel_name] = channel.publish(post)
            except Exception as exc:  # channel code is untrusted
                post.failures.append({"channel": channel_name, "at": now().isoformat(),
                                      "reason": f"{type(exc).__name__}: {exc}"[:200]})
        if results:
            post.state = "published"
            post.published_at = now().isoformat()
        else:
            post.state = "failed"
        self._append(post)
        return results

    def due(self, at: datetime | None = None) -> list[Post]:
        moment = at or now()
        due = []
        for post in self.posts.values():
            if post.state != "approved" or not post.scheduled_for:
                continue
            try:
                when = datetime.fromisoformat(post.scheduled_for)
            except ValueError:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if when <= moment:
                due.append(post)
        return sorted(due, key=lambda p: p.scheduled_for or "")

    def status(self) -> dict:
        counts = {state: 0 for state in QUEUE_STATES}
        for post in self.posts.values():
            counts[post.state] += 1
        return {
            "counts": counts,
            "channels": {name: dict(zip(("available", "reason"), ch.available()))
                         for name, ch in self.channels.items()},
            "failed_details": [
                {"post": p.post_id, "failures": p.failures}
                for p in self.posts.values() if p.failures
            ],
        }
