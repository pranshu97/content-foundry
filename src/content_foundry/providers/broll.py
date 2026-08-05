"""Stock B-roll clients: Pexels + Pixabay + Coverr, aggregated by MultiBrollClient (Ch. 11.5).

Disabled gracefully (NullBrollClient) when no key is set; each scene then falls back to generation.
"""

from __future__ import annotations

import random
import re
from itertools import zip_longest
from typing import Protocol, runtime_checkable


def _download_bytes(url: str) -> bytes:
    import httpx

    resp = httpx.get(url, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


def _interleave(pools: list[list[str]]) -> list[str]:
    """Round-robin merge several result pools (de-duplicated), so no single source dominates and
    each scene draws from a varied mix."""
    out: list[str] = []
    seen: set[str] = set()
    for row in zip_longest(*pools):
        for url in row:
            if url and url not in seen:
                seen.add(url)
                out.append(url)
    return out


# Front-biased page picker: repeated searches for the same keyword pull DIFFERENT clips (much more
# variety across videos) while still usually hitting the most-relevant first page. A source that
# runs out of pages just errors and is skipped by MultiBrollClient / the visuals layer.
_PAGE_WEIGHTS = (6, 3, 1)  # ~60% page 1, ~30% page 2, ~10% page 3


def _pick_page(rng: random.Random, *, base: int = 1) -> int:
    """Return a front-biased page number. ``base`` is 1 for Pexels/Pixabay and 0 for Coverr (0-indexed)."""
    pages = list(range(base, base + len(_PAGE_WEIGHTS)))
    return rng.choices(pages, weights=list(_PAGE_WEIGHTS), k=1)[0]


# Subjects stock sites pad generic queries with even when they are unrelated to the video — a moon
# time-lapse for "busy office", a lipstick close-up for "person smiling". A clip is dropped when its
# OWN tags/slug name one of these AND the query never asked for it, so a clip whose subject the query
# really did request (an astronomy video that queries "moon") is still kept. Deliberately EXCLUDES
# tech-ambiguous words (cloud, star, tree, network, data) so genuine B-roll is never filtered.
_OFF_TOPIC_SUBJECTS = frozenset(
    {
        # celestial / sky scenery
        "moon",
        "lunar",
        "galaxy",
        "galaxies",
        "planet",
        "planets",
        "nebula",
        "cosmos",
        "cosmic",
        "universe",
        "aurora",
        "eclipse",
        "meteor",
        "comet",
        "sunset",
        "sunrise",
        "twilight",
        "dusk",
        # beauty / cosmetics
        "lipstick",
        "makeup",
        "mascara",
        "eyeshadow",
        "eyeliner",
        "cosmetic",
        "cosmetics",
        "skincare",
        "manicure",
        "pedicure",
        "perfume",
        "salon",
        "spa",
        "lipgloss",
        # animals / wildlife
        "cat",
        "cats",
        "kitten",
        "dog",
        "dogs",
        "puppy",
        "pet",
        "pets",
        "wildlife",
        "bird",
        "birds",
        "horse",
        "cow",
        "sheep",
        "goat",
        "insect",
        "insects",
        "butterfly",
        "bee",
        "spider",
        "fish",
        "dolphin",
        "whale",
        "lion",
        "tiger",
        "elephant",
        "monkey",
        "deer",
        "rabbit",
        # nature / travel scenery
        "flower",
        "flowers",
        "floral",
        "blossom",
        "waterfall",
        "beach",
        "ocean",
        "sea",
        "seascape",
        "seaside",
        "mountain",
        "mountains",
        "jungle",
        "forest",
        "meadow",
        "sunflower",
        "tulip",
        "rose",
        "coral",
        "safari",
        "vineyard",
        # food / drink
        "pizza",
        "burger",
        "cake",
        "dessert",
        "cupcake",
        "cocktail",
        "smoothie",
        "sushi",
        "pancake",
        "wine",
        "beer",
        "champagne",
        # romance / celebration clichés
        "wedding",
        "bride",
        "groom",
        "kiss",
        "kissing",
        "romantic",
        "romance",
        "honeymoon",
        "fireworks",
        "confetti",
        "balloon",
        "balloons",
        "cupid",
        "engagement",
        "engaged",
        "proposal",
        "flirt",
        "flirting",
        "cuddle",
        "cuddling",
        "hug",
        "hugging",
        "sweetheart",
        "affection",
        # love / valentine / holidays / greetings (stock "greeting-card" padding)
        "valentine",
        "valentines",
        "love",
        "heart",
        "hearts",
        "dating",
        "couple",
        "couples",
        "girlfriend",
        "boyfriend",
        "christmas",
        "xmas",
        "santa",
        "halloween",
        "easter",
        "thanksgiving",
        "holiday",
        "holidays",
        "festive",
        "festival",
        "birthday",
        "party",
        "celebration",
        "celebrate",
        "anniversary",
        "gift",
        "gifts",
        "present",
        "greeting",
        "greetings",
        # lifestyle / people fluff
        "yoga",
        "meditation",
        "baby",
        "babies",
        "toddler",
        "newborn",
        "fashion",
        "dance",
        "dancing",
        "concert",
        "nightclub",
        "disco",
        "karaoke",
        # medical / anatomy / biology — the classic "diagram"/"chart"/"model"/"scan" mismatch (a stock
        # anatomy diagram padded in for "whiteboard diagram"). Safe for a medical niche: a clip is only
        # dropped when the QUERY itself never used the term (see _off_topic). Tech-ambiguous words
        # (cell, virus, dna, molecule) are deliberately EXCLUDED.
        "anatomy",
        "anatomical",
        "intestine",
        "intestines",
        "intestinal",
        "digestive",
        "gastrointestinal",
        "colon",
        "bowel",
        "stomach",
        "liver",
        "kidney",
        "kidneys",
        "pancreas",
        "bladder",
        "artery",
        "arteries",
        "cardiovascular",
        "respiratory",
        "lung",
        "lungs",
        "skeleton",
        "skeletal",
        "vertebrae",
        "ribcage",
        "pelvis",
        "cranium",
        "esophagus",
        "abdomen",
        "organs",
        "surgery",
        "surgical",
        "surgeon",
        "medical",
        "medicine",
        "clinic",
        "clinical",
        "patient",
        "disease",
        "diagnosis",
        "dental",
        "dentist",
        "tooth",
        "teeth",
        "stethoscope",
        "syringe",
        "vaccine",
        "vaccination",
        "ultrasound",
        "pathology",
        "prescription",
        "pharmacy",
        "biology",
        "biological",
        "bacteria",
        "bacterial",
        "microscope",
        "microscopic",
        "embryo",
        "fetus",
        "hormone",
    }
)


def _off_topic(query: str, meta) -> bool:
    """True when a clip's own tags/slug name a known off-topic stock subject (moon, lipstick, cat,
    sunset…) that the query never asked for — so an unrelated clip the API padded results with is
    dropped, while a clip whose subject the query DID request is kept. No metadata => never off-topic
    (we only drop on positive evidence)."""
    if isinstance(meta, list | tuple):
        meta = " ".join(str(m) for m in meta)
    meta_words = set(re.findall(r"[a-z]+", str(meta).lower()))
    stray = meta_words & _OFF_TOPIC_SUBJECTS
    if not stray:
        return False
    query_words = set(re.findall(r"[a-z]+", (query or "").lower()))
    return bool(stray - query_words)


_SLUG_STOP = frozenset(
    {"http", "https", "www", "com", "pexels", "video", "videos", "photo", "photos"}
)


def _slug_words(url: str) -> str:
    """Pexels clips carry a descriptive page slug ('.../video/woman-applying-lipstick-123/'); flatten
    it to its DESCRIPTIVE words (dropping the domain boilerplate) for the relevance check. Other
    providers pass their tags directly."""
    words = re.findall(r"[a-z]+", (url or "").lower())
    return " ".join(w for w in words if w not in _SLUG_STOP)


# Words too generic to PROVE a clip shows what a beat asked for: almost every stock clip is tagged
# with a person, a body part, or a framing word, so a match on ONLY one of these is not evidence. The
# concrete subject/action words in a beat (office, chart, laptop, server, handshake, whiteboard) are
# what must match — see _clip_ok's high-confidence gate.
_GENERIC_SUBJECTS = frozenset(
    {
        "person",
        "people",
        "man",
        "woman",
        "men",
        "women",
        "guy",
        "girl",
        "boy",
        "kid",
        "child",
        "human",
        "adult",
        "someone",
        "somebody",
        "worker",
        "professional",
        "team",
        "group",
        "crowd",
        "everyone",
        "hand",
        "hands",
        "finger",
        "fingers",
        "arm",
        "arms",
        "face",
        "head",
        "body",
        "closeup",
        "close",
        "shot",
        "footage",
        "video",
        "clip",
        "background",
        "view",
        "scene",
        "angle",
        "indoor",
        "indoors",
        "outdoor",
        "outdoors",
        "camera",
        "looking",
        "using",
        "working",
        "sitting",
        "standing",
        "walking",
        "talking",
        "holding",
    }
)

# Set dressing a stock library tags almost EVERY clip in a tech/office niche with. Matching one of
# these says nothing about whether the clip shows what the beat actually asked for — "tech" and
# "meeting" are on everything, "debrief" is on nothing — so counting them as evidence lets two pieces
# of scenery outvote the one word that carries the shot. Excluded from a beat's SPECIFIC words so the
# decision rests on its discriminating terms (whiteboard, algorithm, resume, debrief, python).
_STOCK_FILLER = frozenset(
    {
        "office",
        "computer",
        "laptop",
        "screen",
        "monitor",
        "desk",
        "room",
        "table",
        "chair",
        "meeting",
        "business",
        "corporate",
        "company",
        "workplace",
        "colleague",
        "employee",
        "staff",
        "tech",
        "technology",
        "digital",
        "modern",
        "professional",
        "indoor",
        "indoors",
        "building",
    }
)

# Ceiling on how many of a beat's specific words a clip must echo. TWO is deliberate: a stock clip
# carries only a handful of broad tags, so demanding three distinct hits is unreachable for almost
# every beat and starves the video of motion (it took run 0021 from ~22 clips down to 6). Precision
# comes from _STOCK_FILLER making those hits MEAN something, not from raising this number.
_MAX_REQUIRED_MATCHES = 2


def _required_matches(specific_count: int) -> int:
    """How many of a beat's DISCRIMINATING words a clip's tags/slug must name to count as relevant.

    Two thirds rounded up, capped at ``_MAX_REQUIRED_MATCHES``, and 1 when the beat offers only one
    or two such words.

    The cap matters more than the fraction. ``_search_terms`` trims every beat to 4 words, so almost
    all of them arrive with the same specific-word count and this function has effectively only two
    settings — which is why nudging it from 2 to 3 collapsed a whole run's stock footage. Relevance is
    therefore bought by EXCLUDING set dressing from the input (see ``_STOCK_FILLER``) rather than by
    demanding more hits: matching "debrief" is worth more than matching "tech" and "meeting" together.

    A one- or two-word beat needs a single match because what survives filtering is already the
    discriminating term, and stock tags name SUBJECTS while rarely echoing a beat's verb or mood
    ("developer, code, laptop" for "developer typing").
    """
    if specific_count <= 2:
        return 1
    return min(_MAX_REQUIRED_MATCHES, -(-2 * specific_count // 3))


_VERB_SUFFIXES = ("ing", "ers", "er", "ed")


def _norm(word: str) -> str:
    """Fold a word onto its rough stem so a tag and a spoken word that mean the same thing match.

    Only trims when at least four characters survive, which keeps the common real cases (servers ->
    server, interviewers -> interview, monitoring -> monitor, pipelines -> pipeline) without
    mangling short words into collisions. Plurals follow the actual English rule rather than a blind
    "es" strip: "pipelines" is "pipeline"+s, so taking "es" would leave "pipelin" and MISS the
    singular — only a stem ending in a sibilant really takes "es" (boxes, matches). Deliberately
    crude: an unmatched pair costs one stock clip and falls back to a generated image, whereas an
    over-eager stem would let an unrelated clip pass.
    """
    for suffix in _VERB_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    if word.endswith("es") and len(word) - 2 >= 4 and word[-3] in "sxzcho":
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) - 1 >= 4:
        return word[:-1]
    return word


def moment_terms(text: str) -> frozenset[str]:
    """The DISCRIMINATING words of the line a shot will actually sit under, stemmed for matching.

    Set dressing and generic subjects are removed for the same reason they are removed from a beat:
    a clip that shares only "laptop" or "office" with the narration has told the viewer nothing.
    """
    return frozenset(
        _norm(w)
        for w in re.findall(r"[a-z]+", (text or "").lower())
        if len(w) >= 3 and w not in _GENERIC_SUBJECTS and w not in _STOCK_FILLER
    )


def _clip_ok(
    query: str,
    meta,
    vocab: frozenset[str] | set[str],
    moment: frozenset[str] = frozenset(),
) -> bool:
    """Keep a candidate clip only when it is NOT an off-topic stock subject the query never asked for
    AND — when we know this video's vocabulary (``vocab``) — its tags/slug (a) actually touch that
    vocabulary, (b) name ENOUGH of THIS beat's SPECIFIC words (see ``_required_matches``), and (c) name
    something the line it will sit under actually says (``moment``). The vocab check stops
    holiday/greeting/unrelated clips that dodge the denylist (e.g. a 'Happy Valentine's Day' clip in a
    software video); the per-beat specific-word check stops a clip which merely shares a generic
    'person/hand/office' word (a honey-scraping clip for an ML-interview beat); the moment check is
    what stops a clip that matches the BEAT but not the moment — a beat is written once for a whole
    scene, so "developer working on laptop coffee shop night" was passing under a line about inference
    latency and hardware bottlenecks. With NO vocabulary we can't positively filter, so keep; but a
    clip with NO tags/slug while a vocabulary IS known is unverifiable (a bare stock URL) and is
    DROPPED. Now that a rejected clip falls back to a GENERATED image, we hold clips to this much
    higher bar rather than show anything off-topic."""
    if _off_topic(query, meta):
        return False
    if not vocab:
        return True
    if isinstance(meta, list | tuple):
        meta = " ".join(str(m) for m in meta)
    meta_words = set(re.findall(r"[a-z]+", str(meta).lower()))
    if not meta_words:
        # A strong video vocabulary IS known, but this candidate carries no describable tags/slug to
        # check against it (a bare stock URL). We can't confirm it is on-topic, and that gap is exactly
        # how off-topic padding — the recurring Valentine's / greeting-card clip that dodges the
        # denylist — sneaks in. Drop it: the candidate pool is large and the scene falls back to its
        # card, so losing one unverifiable clip keeps junk out at no real cost.
        return False
    # HIGH-CONFIDENCE MATCH: the clip must actually name what THIS beat asked for. From the beat we
    # take its SPECIFIC words (concrete subject/action, ignoring generic 'person/hand/shot' filler)
    # and require the clip's own tags/slug to name TWO THIRDS of them (rounded up), and at least TWO
    # whenever the beat offers two. A clip matching one word of a rich beat is a borderline match that
    # reads as filler on screen; rejecting it falls back to a bespoke GENERATED image depicting the
    # WHOLE beat, which is now the more relevant option. Only a genuinely single-word beat can pass on
    # one match.
    specific = {
        w
        for w in re.findall(r"[a-z]+", (query or "").lower())
        if len(w) >= 3 and w not in _GENERIC_SUBJECTS and w not in _STOCK_FILLER
    }
    if specific and len(meta_words & specific) < _required_matches(len(specific)):
        return False
    # THE MOMENT GATE: the beat is written once per SCENE, but a scene runs 45-90 s and carries several
    # different claims, so a clip that satisfies the beat can still be sitting under a line it has
    # nothing to do with. Require the clip to name at least one discriminating word from the words
    # actually spoken over it. Stemmed on both sides so "servers"/"server" and "pipelines"/"pipeline"
    # count. When the line offers no concrete word at all (pure abstraction) there is nothing to
    # verify against and nothing worth showing as stock, so the shot goes to a generated image.
    #
    # ONE HIT IS THE FLOOR, DO NOT RAISE IT. Requiring two was MEASURED on run 0023 and took its stock
    # footage from 4 clips to ZERO. The asymmetry is why: `_search_terms` trims a clip's query to 4
    # words while a narration window carries 16-22 discriminating words, so "share two" is a demand on
    # the CLIP's tiny vocabulary, not on the line's rich one -- the same trap as `_required_matches`.
    if moment and not {_norm(w) for w in meta_words} & moment:
        return False
    return bool(meta_words & vocab)


@runtime_checkable
class BrollClient(Protocol):
    # Read-only on purpose: MultiBrollClient derives `enabled` from its sub-clients, so declaring a
    # settable attribute here would exclude it from the protocol.
    @property
    def enabled(self) -> bool: ...

    def search(self, query: str, *, context: str = "", moment: str = "") -> list[str]:
        """Return candidate downloadable clip URLs for the query (best first; [] if no match).

        ``context`` is an optional bag of words describing the whole video; clips whose tags touch
        nothing in it are dropped. ``moment`` is the narration this shot will actually sit under;
        clips that name nothing it says are dropped too, because a beat is written once per scene
        and cannot tell which of the scene's several claims a clip will land on."""
        ...

    def download(self, url: str) -> bytes: ...


class NullBrollClient:
    """Used when no Pexels key is configured — every scene falls back to generation/card."""

    enabled = False

    def search(self, query: str, *, context: str = "", moment: str = "") -> list[str]:
        return []

    def download(self, url: str) -> bytes:  # pragma: no cover - never called when disabled
        raise RuntimeError("B-roll is disabled")


class PexelsBrollClient:
    enabled = True
    name = "pexels"
    _SEARCH_URL = "https://api.pexels.com/videos/search"

    def __init__(
        self, api_key: str, pool_size: int = 15, *, rng: random.Random | None = None
    ) -> None:
        self._api_key = api_key
        self._pool_size = max(1, pool_size)
        self._rng = rng or random.Random()

    def search(self, query: str, *, context: str = "", moment: str = "") -> list[str]:
        import httpx

        resp = httpx.get(
            self._SEARCH_URL,
            headers={"Authorization": self._api_key},
            params={
                "query": query,
                "per_page": self._pool_size,
                "page": _pick_page(self._rng),
                "orientation": "landscape",
            },
            timeout=30,
        )
        resp.raise_for_status()
        vocab = set(re.findall(r"[a-z]{3,}", context.lower()))
        spoken = moment_terms(moment)
        urls: list[str] = []
        for video in resp.json().get("videos", []):
            files = sorted(video.get("video_files", []), key=lambda f: f.get("width", 0))
            if files and _clip_ok(query, _slug_words(video.get("url", "")), vocab, spoken):
                urls.append(files[-1]["link"])
        return urls

    def download(self, url: str) -> bytes:
        return _download_bytes(url)


class PixabayBrollClient:
    """Free stock video from Pixabay (needs a free API key). A second source so scenes draw from a
    bigger pool and different videos end up looking different."""

    enabled = True
    name = "pixabay"
    _SEARCH_URL = "https://pixabay.com/api/videos/"

    def __init__(
        self, api_key: str, pool_size: int = 15, *, rng: random.Random | None = None
    ) -> None:
        self._api_key = api_key
        self._pool_size = min(200, max(3, pool_size))  # Pixabay requires per_page in [3, 200]
        self._rng = rng or random.Random()

    def search(self, query: str, *, context: str = "", moment: str = "") -> list[str]:
        import httpx

        resp = httpx.get(
            self._SEARCH_URL,
            params={
                "key": self._api_key,
                "q": query,
                "per_page": self._pool_size,
                "page": _pick_page(self._rng),
            },
            timeout=30,
        )
        resp.raise_for_status()
        vocab = set(re.findall(r"[a-z]{3,}", context.lower()))
        spoken = moment_terms(moment)
        urls: list[str] = []
        for hit in resp.json().get("hits", []):
            renditions = hit.get("videos", {})
            for size in ("large", "medium", "small", "tiny"):
                link = (renditions.get(size) or {}).get("url")
                if link:
                    if _clip_ok(query, hit.get("tags", ""), vocab, spoken):
                        urls.append(link)
                    break
        return urls

    def download(self, url: str) -> bytes:
        return _download_bytes(url)


class CoverrBrollClient:
    """Free stock video from Coverr (coverr.co). A third source so scenes draw from an even bigger,
    more varied pool. The key is requested at team@coverr.co, and Coverr asks that you attribute it
    (credit "Videos from Coverr"); it is therefore opt-in (empty key -> not used)."""

    enabled = True
    name = "coverr"
    _SEARCH_URL = "https://api.coverr.co/videos"

    def __init__(
        self, api_key: str, pool_size: int = 15, *, rng: random.Random | None = None
    ) -> None:
        self._api_key = api_key
        self._pool_size = max(1, pool_size)
        self._rng = rng or random.Random()

    def search(self, query: str, *, context: str = "", moment: str = "") -> list[str]:
        import httpx

        resp = httpx.get(
            self._SEARCH_URL,
            params={
                "api_key": self._api_key,
                "query": query,
                "page": _pick_page(self._rng, base=0),  # Coverr pages are 0-indexed
                "page_size": self._pool_size,
                "urls": "true",  # include the mp4 links in the list response
            },
            timeout=30,
        )
        resp.raise_for_status()
        vocab = set(re.findall(r"[a-z]{3,}", context.lower()))
        spoken = moment_terms(moment)
        urls: list[str] = []
        for hit in resp.json().get("hits", []):
            link = (hit.get("urls") or {}).get("mp4")
            if link and _clip_ok(query, [hit.get("title", ""), hit.get("tags", "")], vocab, spoken):
                urls.append(link)
        return urls

    def download(self, url: str) -> bytes:
        return _download_bytes(url)


class MultiBrollClient:
    """Aggregate several B-roll clients into one bigger, varied pool. Resilient: if one source
    errors (e.g. rate-limited), the others still contribute."""

    def __init__(self, clients: list[BrollClient]) -> None:
        self._clients = [c for c in clients if getattr(c, "enabled", False)]

    @property
    def enabled(self) -> bool:
        return bool(self._clients)

    def search(self, query: str, *, context: str = "", moment: str = "") -> list[str]:
        pools: list[list[str]] = []
        for client in self._clients:
            try:
                pools.append(client.search(query, context=context, moment=moment))
            except Exception:  # one source failing must not sink the scene
                pools.append([])
        return _interleave(pools)

    def download(self, url: str) -> bytes:
        return _download_bytes(url)
