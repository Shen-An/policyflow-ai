"""Approvals package: the human-in-the-loop boundary for high-impact actions.

Nothing that leaves the system (an external submission) runs without an approval
whose digest still matches, whose approver still has authority, and whose target
versions are unchanged. This package owns that: the action digest (what the
decision is bound to), the approval state machine, and the atomic
approve->consume->submit handoff.
"""
