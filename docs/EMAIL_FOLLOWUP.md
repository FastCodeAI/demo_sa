# Follow-up email — architecture v1 ready for review

Drop-in reply text below the line. Tweak salutation, sign-off, and the
parts in `[brackets]`. Two versions: a longer one with the architecture
walk-through and what we'd build next, and a tight version for a busy
recipient.

---

## Longer version (≈600 words)

**Subject:** DEMO Scheduler — architecture v1 ready for review and a proposal for the next phase

Hi [name],

Following up on our earlier note: we've built a working **proof of
concept** of the scheduler architecture, end-to-end, on the Q1 2025
dataset. The intent of this PoC was to de-risk the whole stack — not
to ship a production system yet — and to give you something concrete
to react to before we commit to the larger build. Two artifacts
attached for your review:

- **Architecture document** — the full picture, from solver core to
  agent stack, with flowcharts and diagrams for each plane.
- **Screen recording** — a short walk-through of the interactive web
  UI: changing a constraint in plain English, watching the Verifier
  clear it, re-solving, and seeing the agent narrate why a handful of
  orders end up unfilled.

**What the PoC covers**

A working three-plane system on the Q1 2025 dataset (1,824 demand
lines, ~30 M ampoules after backorder roll-in). Every layer is
intentionally minimal but real — the goal was to prove the
architecture, not to optimise any single piece:

- **Solver core.** A two-stage hybrid: a master MILP allocates orders
  to (machine, week) cells across the quarter; a CP-SAT layer lays
  each week's allocation onto the time axis with per-machine and
  global changeover-team no-overlap. A retry-cut loop reconciles the
  two whenever within-week sequencing can't absorb what the master
  asked for. End-to-end status on Q1 today: **98.8 % OTIF, optimal,
  ~6 min wall time**.
- **Versioned constraint catalog.** Every business rule — Piramal's
  monthly band, the no-split organisations, the four-day production-
  to-pack lag, the Farcon ⊕ Dividella mutex, the campaign-coverage
  windows — is a declarative YAML row with a pattern type, parameters,
  and natural-language business rules. Pattern compilers translate
  each row into the right algebraic constraint in either the MILP, the
  CP-SAT, or both. 16 rules live in the catalog today.
- **Agent stack.** A Supervisor classifies each user message and
  routes to one of seven specialist sub-agents (constraint elicitation,
  explanation, what-if, schedule generation, infeasibility,
  disruption-response, clarify). A deterministic Verifier — schema +
  unit + business-rule + optional feasibility check + audit append —
  gates every catalog mutation before it can affect a future solve.
  The language model is the translator and the narrator; the Verifier
  and the solver are the source of truth.
- **Interactive web UI.** A single page with a chat box that drives
  the agent stack, a catalog browser, a variables view, and a
  schedule view that auto-narrates any unfilled orders by reading the
  real plan first and asking the LLM to write prose *over* the
  grounded report (it cannot invent numbers).

**The ask: does this cover your requirements?**

We mapped your 50 requirements onto 15 hard + 3 soft constraints —
all in the catalog. Before we go further, we'd like to confirm:

- Does the constraint set, as enumerated in the architecture document
  and the catalog browser, cover everything Production, Sales, and
  Planning expect the scheduler to honour?
- Are there rules currently handled informally in the team that
  should also be promoted into the catalog?
- Are the four personas the UI explains for (sales, production,
  compliance, planner) the right cut?

**What we propose to build next — production system on top of this PoC**

If the architecture lands well, we'd build the full production system
directly on top of what's already here — the catalog, the solver
stack, the agent layer, and the Verifier all carry forward unchanged.
The current PoC is *interactive*: a planner edits a rule, presses
re-solve, reviews the new plan. The production system turns the same
loop *reactive* and extends it with the operational surface:

- **Disruption detection.** A telemetry feed (ProdAction / Photocells
  / SAP downtime tickets) lands as an event on the agent stack.
- **Auto-replan.** A Disruption-Response agent classifies the event
  (machine down, scrap spike, material short, urgent customer
  swap), drafts the smallest viable plan-patch, runs it through the
  Verifier, and either applies it autonomously or routes it to an
  Approval Queue, depending on the severity of the change.
- **Live operator dashboard.** Shifts see the current plan, the
  agent's running commentary, and any pending approvals — same UI
  shape as today, with live updates.
- **SAP commit path.** Accepted plans push back into SAP with a full
  audit trail (who approved what, when, why) so the schedule and the
  ERP can never drift.

Same architectural principle as the current build: the language model
is opinionated about prose and choice; the Verifier and the solver
remain the source of truth on every number.

**Still open from our side**

The data gaps we flagged earlier are still the longest pole — the
real changeover matrices, throughput rates, shift calendar, BoM, and
the Piramal band clarification. The current PoC ran on best-effort
mocks; numbers will move when those land.

Happy to walk through the architecture and the recording on a call —
30 minutes is plenty.

Best,
[your name]

---

## Short version (≈220 words)

**Subject:** DEMO Scheduler — architecture v1 ready for review

Hi [name],

Quick follow-up: we've built a working **proof of concept** of the
scheduler — end-to-end, on the Q1 2025 dataset — to de-risk the
architecture before we commit to the full build. Sharing it now so
you can react before we go bigger.

**Attached:** the architecture document and a short screen recording
of the interactive UI — natural-language constraint edits, a
deterministic Verifier gating each change, a re-solve, and the agent
narrating why a handful of orders went unfilled.

**Where we are.** Two-stage hybrid solver (master MILP for allocation,
CP-SAT for within-week sequencing), a versioned catalog of 16
declarative constraint rules feeding both stages, and a seven-agent
stack on top with a deterministic Verifier as the trust boundary.
Q1 2025 runs at **98.8 % OTIF, optimal, ~6 min**. Every business rule
lives as data, not code.

**Ask.** Does the constraint set, as enumerated in the doc and
browseable in the UI, cover everything Production, Sales, and Planning
expect the scheduler to honour? Are there informal rules we should
promote into the catalog?

**Next phase.** If the architecture lands well, we'd build the full
production system directly on top of this PoC — same catalog, same
solver stack, same agent layer — extending the loop from
*interactive* to *reactive*: telemetry-driven disruption detection,
auto-replan with Verifier gating, an Approval Queue for governed
changes, an operator dashboard, and a clean SAP write-back path.

Happy to walk through on a 30-min call.

Best,
[your name]
