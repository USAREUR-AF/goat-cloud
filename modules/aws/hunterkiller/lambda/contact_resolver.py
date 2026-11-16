"""
Contact Resolver

Resolves the address that receives notices about a resource, in this order:

1. Tag: the first of the configured contact tag keys that holds an address
2. Registry entry: the `contact` of the registry entry whose repository is the
   resource's repository tag (both normalized before they are compared)
3. IAM user mapping: if the managed-by tag holds an IAM user name rather than
   a tool, that user's Email tag
4. Administrators
"""

import os
import re

import boto3
from compliance_schema import IAC_TYPES, TAG_KEYS, same_repository

_EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

# Mail domains a tag-supplied contact may be in (CONTACT_ALLOWED_DOMAINS,
# comma-separated). Empty = any domain. With a list set, a contact address
# outside it is ignored and the warning goes to the next tier.
_ALLOWED_DOMAINS = {
    d.strip().lower().lstrip("@") for d in os.environ.get("CONTACT_ALLOWED_DOMAINS", "").split(",") if d.strip()
}


def _allowed(email, allowed_domains=None):
    """True when the address is in an allowed domain (or no list is set)."""
    domains = _ALLOWED_DOMAINS if allowed_domains is None else allowed_domains
    if not domains:
        return True
    return email.rsplit("@", 1)[-1].lower() in domains


# Cache IAM user email lookups (username -> email or None)
_iam_email_cache = {}


def resolve_contact(tags, contact_tag_keys, admin_emails, registry_entries=None):
    """
    Resolve the contact email for a resource.

    Args:
        tags: dict of resource tags
        contact_tag_keys: ordered list of tag keys to check for email
        admin_emails: fallback admin email list
        registry_entries: registry entries (dicts with repo_url and, when the
            repository states one, contact)

    Returns:
        tuple: (email_address, source_description)
            source: "tag:Contact", "registry:<repo_slug>", "iam:username",
            "admin_fallback" or "no_contact"
    """
    # 1: Contact tag on the resource
    for key in contact_tag_keys:
        value = tags.get(key, "").strip()
        if value and _EMAIL_PATTERN.match(value):
            if _allowed(value):
                return value, f"tag:{key}"
            print(f"  Contact tag {key} names an address outside the allowed domains; ignored")

    # 2: Contact of the registry entry of the resource's repository
    repository = tags.get(TAG_KEYS["repository"], "")
    for entry in registry_entries or []:
        contact = (entry.get("contact") or "").strip()
        if contact and _EMAIL_PATTERN.match(contact) and same_repository(repository, entry.get("repo_url", "")):
            return contact, f"registry:{entry.get('repo_slug') or entry.get('repo_url')}"

    # 3: managed-by names an IAM user rather than a tool -> that user's Email tag
    managed_by = tags.get(TAG_KEYS["managed_by"], "").strip()
    if managed_by and managed_by.lower() not in IAC_TYPES:
        email = _lookup_iam_user_email(managed_by)
        if email and _allowed(email):
            return email, f"iam:{managed_by}"

    # 4: Fallback to all cloud admins
    if admin_emails:
        return ",".join(admin_emails), "admin_fallback"

    return "", "no_contact"


def _lookup_iam_user_email(username):
    """
    Look up an IAM user's Email tag. Returns email string or None.
    Results are cached for the Lambda invocation.
    """
    if username in _iam_email_cache:
        return _iam_email_cache[username]

    try:
        iam = boto3.client("iam")
        response = iam.list_user_tags(UserName=username)
        for tag in response.get("Tags", []):
            if tag["Key"].lower() == "email":
                email = tag["Value"].strip()
                if _EMAIL_PATTERN.match(email):
                    _iam_email_cache[username] = email
                    return email

        _iam_email_cache[username] = None
        return None

    except Exception as e:
        print(f"  IAM user lookup failed for '{username}': {e}")
        _iam_email_cache[username] = None
        return None
