"""Refills: what a playground's lessons spend, put back on every run, so that Re-seed starts them over.

    seed/refills.yaml       {<refill>: {workspace: <name>, member: <source id>, counters: cost}}

A refill is the budget one member of an AI workspace spends in a lesson (a sandbox that a lesson empties to meet the 429).
The gateway keeps what a member has spent in the core's Redis, one hash for each of their budgets, and the hash says which
subscription it counts for. The budget is a rolling period, so nothing but time gives it back: a refill deletes the hashes
of that member's subscription, and the member starts the period over with the whole budget. It touches nothing else.
A refill is the lab's: it reaches the Redis of the core, which a platform other than the lab does not give. Where the
seed has no address for it (or none it can reach), it says so and goes on.

    counters: cost      the cost limit (ratelimit:<workspace>:<application>:cost-rl, with `subscription` among its fields)

The seed finds the member in the workspace it seeds (`of()`, like a control), and refills them once the workspace is
deployed and the gateway serves it (`run()`). A refill is a convenience of starting over, not something the platform needs,
so whatever stops it is told and never stops the seed: a member who is not there yet, a Redis nobody can reach, an error
Redis answers with. What the declaration gets wrong (a typo in the workspace, a member the workspace file does not list) is
an error, as for a control, and `finish()` stops the seed on a refill no module found its workspace for.
"""

import os

from .common import SeedError, load, log, seed_file

SECTION = "refills"
FILE = "refills.yaml"
KEYS = ("workspace", "member", "counters")
COUNTERS = {"cost": "ratelimit:*:cost-rl"}   # the pattern of the keys of each kind of counter the gateway keeps in Redis
OWNER = "subscription"                       # the field of a counter that says whose it is
CHUNK = 500                                  # keys read in one round trip
TIMEOUT = 10                                 # seconds to reach Redis and to wait for each answer
AFTER = "The budget is whole again when its period ends"

_matched = set()    # the refills whose workspace a module has found


class Refill:
    """One refill of seed/refills.yaml."""

    def __init__(self, name, spec):
        where = f"{FILE}: {name}"
        if not isinstance(spec, dict):
            raise SeedError(f"{where}: expected the workspace, the member and the counters to refill")
        unknown = set(spec) - set(KEYS)
        if unknown:
            raise SeedError(f"{where}: {', '.join(sorted(unknown))} is not a key of a refill ({', '.join(KEYS)})")
        if not isinstance(spec.get("workspace"), str) or not spec["workspace"]:
            raise SeedError(f"{where}: workspace is the name of the AI workspace")
        if not isinstance(spec.get("member"), str) or not spec["member"]:
            raise SeedError(f"{where}: member is the source id of one of its members")
        if spec.get("counters") not in COUNTERS:
            raise SeedError(f"{where}: counters is one of {', '.join(COUNTERS)}")
        self.id, self.workspace, self.member, self.counters = name, spec["workspace"], spec["member"], spec["counters"]


# --- The declaration ---------------------------------------------------------------------------

def declared():
    """Every refill of seed/refills.yaml, in the order of the file; none when the seed has no such file."""
    path = seed_file(FILE)
    if not path:
        return []
    spec = load(path)
    if not isinstance(spec, dict):
        raise SeedError(f"{FILE}: expected the refills, by name")
    return [Refill(str(name), body) for name, body in spec.items()]


def reset():
    """Forgets which refills were found: a new seed starts from nothing."""
    _matched.clear()


def begin():
    """Starts a seed: every refill is read before anything is changed. Returns them."""
    reset()
    return declared()


def of(workspace, members):
    """The refills of the workspace a module is about to seed, whose members are the source ids `members` (those of its
    file), which it marks as found. A refill of a member the file does not list is an error."""
    found = [refill for refill in declared() if refill.workspace == workspace]
    for refill in found:
        if refill.member not in members:
            raise SeedError(f"{FILE}: {refill.id} refills {refill.member}, who is not a member of AI workspace '{workspace}'")
    _matched.update(refill.id for refill in found)
    return found


def finish():
    """Ends a seed whose refills all found their workspace."""
    left = [refill for refill in declared() if refill.id not in _matched]
    if left:
        raise SeedError("; ".join(f"{FILE}: {refill.id} names AI workspace '{refill.workspace}', which no seed file has"
                                  for refill in left))


# --- Redis ---------------------------------------------------------------------------------------

def connect():
    """The core's Redis, from REDIS_URL and REDIS_PASSWORD (the seed already has them for the LLM proxy's semantic cache).
    Nothing is read before this is called, and the library is imported here: only a playground with a refill needs it."""
    url, password = os.getenv("REDIS_URL"), os.getenv("REDIS_PASSWORD")
    if not url:
        raise SeedError("REDIS_URL is not set")
    import redis

    return redis.Redis.from_url(url, decode_responses=True, socket_timeout=TIMEOUT, socket_connect_timeout=TIMEOUT,
                                **({"password": password} if password else {}))


def owned(client, pattern, subscription):
    """The keys of `pattern` whose hash says it counts for this subscription, and no others. Without a subscription it
    refuses: a counter that says none would be taken for that person's."""
    if not subscription:
        raise SeedError("no subscription to count the budget of")
    keys = list(client.scan_iter(match=pattern, count=CHUNK))
    mine = []
    for start in range(0, len(keys), CHUNK):
        chunk = keys[start:start + CHUNK]
        batch = client.pipeline(transaction=False)
        for key in chunk:
            batch.hget(key, OWNER)
        # A key that is not a hash answers with an error in its place: it is no one's counter
        mine += [key for key, answer in zip(chunk, batch.execute(raise_on_error=False)) if answer == subscription]
    return mine


def drop(client, refill, subscription):
    """Deletes the counters of the refill that count for the subscription. Returns how many there were. A member with no
    subscription has no counters to find: nothing is deleted, and the refill says so."""
    if not subscription:
        raise SeedError(f"{refill.member} has no subscription in {refill.workspace}")
    keys = owned(client, COUNTERS[refill.counters], subscription)
    if keys:
        client.delete(*keys)
    return len(keys)


def told(error):
    """What went wrong, on one line, without the password and the full stop the sentence around it supplies."""
    text = str(error) if isinstance(error, SeedError) else f"{type(error).__name__}: {error}".removesuffix(": ")
    password = os.getenv("REDIS_PASSWORD")
    return " ".join((text.replace(password, "***") if password else text).split()).rstrip(".")


def run(found, subscriptions):
    """Refills the members of the refills `found`, whose subscriptions are `subscriptions` ({source id: subscription id}
    of those the workspace has). What goes wrong is said and the seed goes on."""
    waiting = []
    for refill in found:
        if refill.member in subscriptions:
            waiting.append(refill)
        else:
            log(SECTION, f"! {refill.id}: {refill.member} is not a member of {refill.workspace} yet: nothing to refill")
    if not waiting:
        return
    try:
        client = connect()
    except Exception as e:  # noqa: BLE001 - Redis is not the seed's to fix: whatever it says is told, and the seed goes on
        log(SECTION, f"! Could not refill {', '.join(refill.id for refill in waiting)}: {told(e)}. {AFTER}")
        return
    try:
        for refill in waiting:
            try:
                removed = drop(client, refill, subscriptions[refill.member])
            except Exception as e:  # noqa: BLE001 - as above
                log(SECTION, f"! Could not refill {refill.id}: {told(e)}. {AFTER}")
                continue
            if removed:
                log(SECTION, f"✓ {refill.id}: the {refill.counters} budget of {refill.member} is whole again")
            else:
                log(SECTION, f"{refill.id}: {refill.member} has spent nothing in this period")
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001 - closing a connection that failed
            pass
