# Triage Labels

The skills speak in terms of five canonical triage roles, plus the lead layer's two. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |
| (orchestrate: lead)        | `needs-lead`         | Waiting on the lead session              |
| (orchestrate: lead)        | `lead`               | A ticket the lead owns                   |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

`needs-lead` is the one role that may share a ticket with another state role: it may sit alongside `ready-for-agent` on a claimed stop (a deliberate exception to "exactly one state role" while the ticket is claimed), and it stands alone on an ADR-conflict ticket whose PR is held.

`lead` marks tickets the lead owns: decision PRs, and drafts filed `needs-triage` + `lead`. It never appears with `ready-for-agent`.

Setup creates both labels when they are missing.

Edit the "Label in our tracker" column to match whatever vocabulary you actually use.
