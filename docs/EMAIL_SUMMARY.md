# Email-ready summary

Drop-in reply text below the line. Tweak salutation, sign-off, and the parts in `[brackets]`. Three versions: a tightest ack-and-questions one (≈150 words), a tight read-back (≈250 words), and a fuller one (≈600 words) if the recipient wants substance.

---

## Tightest version (≈150 words) — ack + questions only

**Subject:** DEMO Scheduler — quick ack and a few questions

Hi [name],

Thanks for the materials — we've worked through them.

We're framing this as a **two-stage capacitated lot-sizing and scheduling problem** on parallel non-identical machines, with sequence-dependent setup times and a rolling-horizon re-plan. The objective is to maximise rating-weighted on-time fulfilment; changeover hours, idle time, and lateness enter as configurable penalty terms.

On the modernisation side — thanks for the legacy-app stats (Laravel 5.7 / PHP 7.4, Vue 2, Lucid/DDD pattern, ~93.5 k LOC across 269 endpoints, 192 Lucid features, 56 models). The new platform would host both the scheduler and the existing operational modules (lots, machines, work-centers, shift output, format-change actions, SAP imports, activity logging). Once we get the workflow walk-through and screenshots / video, we'll send a separate read-back on the migration approach.

Before we go deeper, a few questions:

1. **Piramal band.** The Piramal demand in the Excel works out to 2.0–2.8 M/month vs the stated 1.8–2.0 M band. Is that band per legal entity, per ship-to, or aggregate?
2. **Operating data.** Are format-changeover matrices and per-machine throughput rates available, or should we derive them from ProdAction / Photocells?
3. **Master data.** Is the BoM and material-availability feed in place, or part of the build?
4. **PoC scope.** Stay on glass ampoules, or include plastic ampoules / bottles in the same model?
5. **Modernisation.** Could you share the workflow walk-through and screenshots / video for the existing app? And: treat scheduler + refactor as one deliverable, or sequence them (scheduler PoC first, then strangler-pattern migration of the remaining modules)?

Happy to set up a 30-min call.

Best,
[your name]

---

## Short version (≈250 words)

**Subject:** DEMO Production & Packaging Scheduler — our understanding and proposed approach

Hi [name],

Thanks for the materials. Quick read-back on what we've understood and how we'd approach it.

**What this is.** A two-stage scheduling problem — semi-finished production feeding into packaging — for ~1,800 demand lines totalling 90.4 M ampoules in the 2025 PoC. Three structural realities shape the model: demand is highly concentrated (Piramal alone = 36 %, top 5 customers = 83 %), Marchesini GL carries 61 % of volume on its own, and 2ml / 5ml / 10ml together cover 97 % of the format mix. We've mapped your 50 requirements to 15 hard + 3 soft constraints across four planning horizons (annual → quarterly → weekly → real-time).

**What we're solving.** Two coupled questions for every order: *when* to produce its semi-finished form, and *where, when, and how* to package it. The plan has to live with real frictions — machines that share an operator team and can't run at the same time, a changeover crew that can only handle one machine at a time, mandatory lead time between production and packaging, customer volume bands that must hold every month, orders for humanitarian organisations that can't be split, and shelf-life windows that cap how far ahead we can produce. The objective is a tunable balance: ship the highest-rated and VIP customers first, minimise changeover hours, idle time, and lateness — with every weight configurable so Sales and Production can adjust the trade-off without touching code.

**How.** Hybrid MILP (assignment, fairness) + CP-SAT (sequencing) + LNS (live repair) under an LLM-agent orchestration with a verifier-in-the-loop, so adding or changing a rule is a *configuration* operation, not a code change. LLMs translate, explain, and validate — the solver is always the source of truth on numbers.

**What we need from you to start building**

- Format-changeover time matrices per packaging line.
- Throughput rates (units/hour) per machine × format — from ProdAction.
- Shift and maintenance calendar per machine.
- BoM master and a material-availability feed (req #26).
- Shelf life and scrap / yield rates per semi-finished product.
- Due-date / pack-slot calendar (slot *counts* are present in the Excel; calendar *dates* are not).
- Glass-machine volume thresholds for the line-selection rule (req #31).
- **Clarification on Piramal:** the dataset shows 32.9 M Piramal demand, which translates to 2.0–2.8 M/month — every month exceeds the stated 1.8–2.0 M band. Is the band per legal entity, per ship-to, or aggregate?

Happy to walk through on a call.

Best,
[your name]

---

## Fuller version (≈600 words)

**Subject:** DEMO Production & Packaging Scheduler — our understanding and proposed approach

Hi [name],

Thanks for the materials — the Excel, the 50-requirement PDF, and the planning-overview deck. We've worked through them and want to read back what we've understood, what we plan to solve, and how, before we start building.

**What we've understood**

This is a two-stage operations-research problem: production of semi-finished glass ampoules feeds into a downstream packaging operation. The 2025 PoC dataset has 1,824 demand lines totalling ~90.4 M ampoules, and three realities shape the model:

- **Demand is highly concentrated.** Piramal alone is 36 % of 2025 volume; top 5 customers are 83 %. The fair-distribution rule mostly bites within this cluster.
- **Marchesini GL is the single biggest capacity exposure.** 61 % of volume routes there; Farcon + Dividella share an operator team and can't run simultaneously.
- **Format mix is tight.** 2ml / 5ml / 10ml = 97 % of volume — those are the changeover pairs that drive the schedule cost.

We've mapped your 50 requirements into 15 hard and 3 soft constraints, organised across four planning horizons (annual → quarterly → weekly → real-time) so the same plan stays coherent at every resolution.

**What we're solving**

For every order, the system answers two coupled questions:

1. **When to produce the semi-finished form** — which week and in what campaign size, sequenced so one active ingredient runs at a time and each campaign covers six or nine months of demand depending on the product's shelf life.
2. **Where, when, and how to package it** — which of the four lines (Marchesini, Farcon, Dividella, Partena), which week, which container format.

Both schedules have to live with real-world frictions: some machines share an operator team and can't run simultaneously, the same is true of the changeover crew across all packaging machines, production must finish a few days before packaging starts, customer volume bands have to hold every month (e.g. Piramal at 1.8–2.0 M ampoules), orders for humanitarian organisations (Unicef, Imres, ICRC, IDA) can't be split across weeks, and shelf-life caps how long a semi-finished product can wait before it has to be packaged.

The objective is a tunable balance: maximise demand fulfilment for the highest-rated and VIP customers first, minus changeover hours, idle hours, and lateness. Every weight is configurable, so Sales and Production can dial the trade-off — for instance, accept more changeovers to ship more VIP volume, or accept more idle time to keep changeovers down — without touching code.

**How we propose to solve it**

A three-tier hybrid:

- **Declarative constraint catalog.** Every business rule is a versioned data row, not code. A new or changed rule flows through an automatic verifier (schema → unit → feasibility → business-rule check) before commit. Changing the Piramal band or the team-size assumption is a configuration edit, not a release.
- **Solver ensemble.** MILP master (HiGHS / Gurobi) for assignment + customer-fairness; CP-SAT (OR-Tools) for sequencing and sequence-dependent setup; LNS for fast local repair when telemetry reports a stoppage; discrete-event simulation to validate the schedule before any SAP push.
- **LLM agent orchestration on top** (LangGraph + MCP services). Specialist agents handle constraint elicitation from natural language, schedule generation, infeasibility narration (OptiChat-style), what-if analysis (OptiGuide-style), live disruption response (ALAS-style local repair), and persona-tuned explanations (shift view, sales tracker, KPI dashboard). LLMs translate, explain, and validate — they are never the source of truth on a number; the solver is.

Decomposition runs in tiers: annual feasibility (seconds) → quarterly MILP (minutes) → weekly CP-SAT (seconds-to-minutes) → real-time LNS repair (< 1 min). Every Monday the cycle re-fires with last week's actuals replacing last week's plan.

**What we need from you to start building**

The Excel covers demand and ratings well, but a few inputs are currently blocking:

1. Format-changeover time matrices per packaging line.
2. Throughput rates (units/hour) per machine × format — from ProdAction.
3. Shift and maintenance calendar per machine.
4. BoM master and a material-availability feed (req #26).
5. Shelf life and scrap / yield rates per semi-finished product.
6. Due-date / pack-slot calendar (slot *counts* are present; calendar *dates* are not).
7. Glass-machine volume thresholds for the line-selection rule (req #31).
8. **A clarification on Piramal:** the dataset shows 32.9 M Piramal demand, which translates to 2.0–2.8 M/month — every month is over the 1.8–2.0 M band. Is that band per legal entity, per ship-to, or aggregate?

**Suggested next step**

Once those inputs are in hand, we'd like to start with a one-quarter vertical slice (Q1 2025, top 3 customers, 2ml + 5ml, Marchesini + Farcon) that exercises the full architecture end-to-end before scaling to the complete problem.

Happy to set up a working session whenever suits you.

Best,
[your name]
