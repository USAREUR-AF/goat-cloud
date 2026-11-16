"""Tests for hunterkiller contact_resolver.py."""

from unittest.mock import patch

from contact_resolver import _EMAIL_PATTERN, resolve_contact

ADMIN_EMAILS = ["admin@example.com"]
TAG_KEYS = ["Contact", "OwnerEmail", "Owner"]


class TestResolveContact:
    def test_direct_contact_tag(self):
        tags = {"Contact": "user@example.com"}
        email, source = resolve_contact(tags, TAG_KEYS, ADMIN_EMAILS)
        assert email == "user@example.com"
        assert source == "tag:Contact"

    def test_owner_email_tag(self):
        tags = {"OwnerEmail": "owner@example.org"}
        email, source = resolve_contact(tags, TAG_KEYS, ADMIN_EMAILS)
        assert email == "owner@example.org"
        assert source == "tag:OwnerEmail"

    def test_first_matching_tag_wins(self):
        tags = {"Contact": "first@example.com", "OwnerEmail": "second@example.com"}
        email, source = resolve_contact(tags, TAG_KEYS, ADMIN_EMAILS)
        assert email == "first@example.com"
        assert source == "tag:Contact"

    def test_invalid_email_in_tag_skipped(self):
        tags = {"Contact": "not-an-email", "OwnerEmail": "valid@example.com"}
        email, source = resolve_contact(tags, TAG_KEYS, ADMIN_EMAILS)
        assert email == "valid@example.com"
        assert source == "tag:OwnerEmail"

    @patch("contact_resolver._lookup_iam_user_email")
    def test_managed_by_iam_user(self, mock_lookup):
        mock_lookup.return_value = "iam-user@corp.example"
        tags = {"ManagedBy": "jsmith"}
        email, source = resolve_contact(tags, TAG_KEYS, ADMIN_EMAILS)
        assert email == "iam-user@corp.example"
        assert source == "iam:jsmith"

    @patch("contact_resolver._lookup_iam_user_email")
    def test_managed_by_terraform_skipped(self, mock_lookup):
        tags = {"ManagedBy": "terraform"}
        _email, source = resolve_contact(tags, TAG_KEYS, ADMIN_EMAILS)
        mock_lookup.assert_not_called()
        assert source == "admin_fallback"

    def test_admin_fallback(self):
        tags = {}
        email, source = resolve_contact(tags, TAG_KEYS, ADMIN_EMAILS)
        assert email == "admin@example.com"
        assert source == "admin_fallback"

    def test_no_admin_no_contact(self):
        tags = {}
        email, source = resolve_contact(tags, TAG_KEYS, [])
        assert email == ""
        assert source == "no_contact"

    def test_multiple_admins_joined(self):
        admins = ["a@example.com", "b@example.com"]
        tags = {}
        email, source = resolve_contact(tags, TAG_KEYS, admins)
        assert email == "a@example.com,b@example.com"
        assert source == "admin_fallback"


class TestEmailPattern:
    def test_valid(self):
        assert _EMAIL_PATTERN.match("user@example.com")
        assert _EMAIL_PATTERN.match("a.b+c@example.org")

    def test_invalid(self):
        assert _EMAIL_PATTERN.match("not-email") is None
        assert _EMAIL_PATTERN.match("@example.com") is None
        assert _EMAIL_PATTERN.match("user@") is None


class TestAllowedDomains:
    def test_any_domain_when_no_list(self):
        import contact_resolver

        email, source = contact_resolver.resolve_contact(
            {"Contact": "a@outside.example"}, ["Contact"], ["admin@example.com"]
        )
        assert (email, source) == ("a@outside.example", "tag:Contact")

    def test_address_outside_the_list_falls_back_to_admins(self):
        from unittest.mock import patch

        import contact_resolver

        with patch.object(contact_resolver, "_ALLOWED_DOMAINS", {"example.com"}):
            email, source = contact_resolver.resolve_contact(
                {"Contact": "a@outside.example"}, ["Contact"], ["admin@example.com"]
            )
            assert (email, source) == ("admin@example.com", "admin_fallback")
            email, source = contact_resolver.resolve_contact(
                {"Contact": "Owner@Example.com"}, ["Contact"], ["admin@example.com"]
            )
            assert (email, source) == ("Owner@Example.com", "tag:Contact")
            # A look-alike domain is not the allowed domain
            email, source = contact_resolver.resolve_contact(
                {"Contact": "a@example.com.outside.example"}, ["Contact"], ["admin@example.com"]
            )
            assert source == "admin_fallback"


def test_module_reads_only_the_contact_tag_by_default():
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "modules" / "aws" / "hunterkiller" / "variables.tf").read_text()
    block = re.search(r'variable "contact_tag_keys" \{(.*?)\n\}', source, re.DOTALL).group(1)
    assert re.search(r'default\s*=\s*\["Contact"\]', block)


REGISTRY = [
    {"repo_slug": "other", "repo_url": "https://git.example/example-org/other", "contact": "other@example.invalid"},
    {"repo_slug": "app", "repo_url": "https://git.example/example-org/app", "contact": "app-team@example.invalid"},
    {"repo_slug": "silent", "repo_url": "https://git.example/example-org/silent", "contact": None},
]


class TestRegistryEntryContact:
    """Order: the tag on the resource, then the registry entry, then the administrators."""

    def test_tag_wins_over_the_registry_entry(self):
        tags = {"Contact": "tag@example.invalid", "Repository": "https://git.example/example-org/app"}
        assert resolve_contact(tags, TAG_KEYS, ADMIN_EMAILS, registry_entries=REGISTRY) == (
            "tag@example.invalid",
            "tag:Contact",
        )

    def test_registry_entry_when_no_tag(self):
        tags = {"Repository": "https://git.example/example-org/app", "ManagedBy": "terraform"}
        assert resolve_contact(tags, TAG_KEYS, ADMIN_EMAILS, registry_entries=REGISTRY) == (
            "app-team@example.invalid",
            "registry:app",
        )

    def test_repository_forms_are_normalized_before_they_are_compared(self):
        tags = {"Repository": "git@git.example:example-org/app.git", "ManagedBy": "terraform"}
        email, source = resolve_contact(tags, TAG_KEYS, ADMIN_EMAILS, registry_entries=REGISTRY)
        assert (email, source) == ("app-team@example.invalid", "registry:app")

    def test_administrators_when_the_entry_has_no_contact(self):
        tags = {"Repository": "https://git.example/example-org/silent", "ManagedBy": "terraform"}
        assert resolve_contact(tags, TAG_KEYS, ADMIN_EMAILS, registry_entries=REGISTRY) == (
            "admin@example.com",
            "admin_fallback",
        )

    def test_administrators_when_no_entry_matches(self):
        tags = {"Repository": "https://git.example/example-org/unknown", "ManagedBy": "terraform"}
        assert resolve_contact(tags, TAG_KEYS, ADMIN_EMAILS, registry_entries=REGISTRY)[1] == "admin_fallback"

    def test_resource_without_repository_matches_no_entry(self):
        entries = [{"repo_slug": "x", "repo_url": "", "contact": "x@example.invalid"}]
        assert resolve_contact({"ManagedBy": "terraform"}, TAG_KEYS, ADMIN_EMAILS, registry_entries=entries)[1] == (
            "admin_fallback"
        )

    @patch("contact_resolver._lookup_iam_user_email")
    def test_a_tool_name_is_never_looked_up_as_an_iam_user(self, mock_lookup):
        for tool in ("terraform", "opentofu", "pulumi", "manual"):
            assert resolve_contact({"ManagedBy": tool}, TAG_KEYS, ADMIN_EMAILS)[1] == "admin_fallback"
        mock_lookup.assert_not_called()
