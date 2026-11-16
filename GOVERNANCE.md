# Governance

## Roles

| Role | Who | What they can do |
|---|---|---|
| **Maintainers** | the members of the GitHub team `goat-owners` | administer the repository, approve and merge pull requests, cut releases, decide project-level questions |
| **Code owners** | listed in `CODEOWNERS` | their approval is required on pull requests touching the paths they own |
| **Contributors** | anyone | open issues and pull requests under [CONTRIBUTING.md](CONTRIBUTING.md) |

## Decisions

- **Day-to-day technical decisions** are made by the contributor doing the work, subject to pull-request review.
- **Changes to the tag contract or the report schema** keep existing consumers working. A change that cannot is a project-level decision.
- **Project-level decisions** (licence, scope, governance, releases) are made by consensus of the maintainers. Where consensus is not reached after discussion, the maintainers decide by simple majority.

## Releases

Releases are tagged `vX.Y.Z` from `main` by a maintainer after CI passes. Pre-1.0, minor versions may change interfaces; the release notes say so.

## Code of conduct

[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) applies in every project space. Reports go to the maintainers through the private channel in [SECURITY.md](SECURITY.md).
