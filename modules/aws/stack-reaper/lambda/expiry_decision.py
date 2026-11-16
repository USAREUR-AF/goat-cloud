"""When a registered stack is due for teardown. One rule for the reconciler and the reaper.

The registry entry decides whether a stack expires and when: its `lifecycle`
must be temporary or experiment and its `expires_at` must be readable. An
entry that is persistent, names another lifecycle, or states no expiry is
never torn down.

Resource tags can only make the decision more conservative. Every live
resource whose Repository tag names the stack is read:
  - a resource with no ExpiresAt, an unreadable one, or a Lifecycle tag that
    is not temporary/experiment blocks the teardown;
  - resource expiries that disagree with each other block it and are reported;
  - a resource expiry later than the entry's delays the teardown to it;
  - an earlier one changes nothing.
"""

from datetime import timedelta

from compliance_contract import normalize_repository, parse_expires_at, same_repository

EXPIRING_LIFECYCLES = frozenset({"temporary", "experiment"})
EXPIRES_AT_TOLERANCE = timedelta(minutes=5)
_MAX_TAG_FILTER_VALUES = 20


def repository_spellings(value):
    """Spellings of one repository address that a tag may carry verbatim.

    For exact-match tag queries; compare the results with same_repository.
    """
    canonical = normalize_repository(value)
    spellings = [v for v in [(value or "").strip() if isinstance(value, str) else ""] if v]
    if canonical.startswith("https://"):
        host, _, path = canonical[len("https://") :].partition("/")
        spellings += [canonical, canonical + "/", canonical + ".git"]
        if path:
            spellings += [f"git@{host}:{path}.git", f"git@{host}:{path}", f"ssh://git@{host}/{path}.git"]
    return list(dict.fromkeys(spellings))


def resources_of_repository(tagging, repo_url):
    """Tag maps of live resources whose Repository tag names this repository.

    Raises on an API error: the caller must not read a partial answer as
    "no resources".
    """
    if not normalize_repository(repo_url):
        return []
    values = repository_spellings(repo_url)[:_MAX_TAG_FILTER_VALUES]
    found = []
    paginator = tagging.get_paginator("get_resources")
    for page in paginator.paginate(TagFilters=[{"Key": "Repository", "Values": values}], ResourcesPerPage=100):
        for resource in page.get("ResourceTagMappingList", []):
            tags = {t.get("Key", ""): t.get("Value", "") for t in resource.get("Tags", [])}
            if same_repository(tags.get("Repository", ""), repo_url):
                found.append({"arn": resource.get("ResourceARN", ""), "tags": tags})
    return found


def entry_expiry(entry):
    """(expires_at, reason): the entry's own expiry, or None with why it does not expire."""
    lifecycle = entry.get("lifecycle")
    if not isinstance(lifecycle, str) or lifecycle.strip().lower() not in EXPIRING_LIFECYCLES:
        return None, f"registry entry lifecycle is {str(lifecycle)[:64]!r}, not temporary or experiment"
    expires_at = parse_expires_at(entry.get("expires_at"))
    if expires_at is None:
        return None, "registry entry states no readable expires_at"
    return expires_at, ""


def decide(entry, resources):
    """Decide what the stack's expiry is.

    Returns a dict with `status` one of:
      persistent   — the entry does not expire; never tear down
      no_resources — nothing tagged for this repository is live
      blocked      — a resource makes teardown unsafe; `problems` says why
      expires      — tear down at `expires_at`
    """
    expires_at, reason = entry_expiry(entry)
    if expires_at is None:
        return {"status": "persistent", "reason": reason}
    if not resources:
        return {"status": "no_resources", "reason": "no live resource carries this repository"}

    problems = []
    resource_expiries = []
    for resource in resources:
        arn, tags = resource["arn"], resource["tags"]
        lifecycle = tags.get("Lifecycle", "")
        if lifecycle and lifecycle.strip().lower() not in EXPIRING_LIFECYCLES:
            problems.append(f"{arn}: Lifecycle={lifecycle[:64]!r}")
            continue
        if "ExpiresAt" not in tags:
            problems.append(f"{arn}: no ExpiresAt")
            continue
        moment = parse_expires_at(tags["ExpiresAt"])
        if moment is None:
            problems.append(f"{arn}: unreadable ExpiresAt")
            continue
        resource_expiries.append(moment)

    if resource_expiries and max(resource_expiries) - min(resource_expiries) > EXPIRES_AT_TOLERANCE:
        values = sorted({m.isoformat() for m in resource_expiries})
        problems.append(f"resources state different ExpiresAt values: {', '.join(values)}")

    if problems:
        return {"status": "blocked", "reason": "; ".join(problems), "problems": problems}

    return {"status": "expires", "expires_at": max([expires_at, *resource_expiries])}
