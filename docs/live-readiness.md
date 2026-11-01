# Live readiness checklist (HANDBOOK 13, Phase 11)

Owner only. Real trading cannot be considered until every box is ticked and the sign-off line is filled in. The code
(`derivbot.guard.real_guard`) reads this file: a single unticked box, a missing box, or a missing sign-off fails the
checklist condition. Agents never tick these boxes.

- [ ] Gate 1 reported and the result recorded in docs/gates.md
- [ ] Edge test (Gate 1b) passed on a locked holdout, control experiment passed
- [ ] Forward demo trading for several weeks with zero unreconciled discrepancies (Gate 2)
- [ ] Independent review of the risk engine, executor and security (docs/review-risk-executor.md, docs/review-security.md) finished and findings closed (Phase 10)
- [ ] Written maximum loss the owner can lose entirely, recorded in config/risk.json
- [ ] Risk limits in config/risk.json set by the owner and reviewed
- [ ] Real-account PAT created with the minimum scope; separate from the demo PAT
- [ ] Deriv terms on automated trading checked for the owner's account type and jurisdiction
- [ ] Restore drill and kill-switch drill done on the deployed host
- [ ] Owner understands the first live stage is a trial with an amount that can be lost entirely

Owner sign-off: (name and date, written by the owner)
