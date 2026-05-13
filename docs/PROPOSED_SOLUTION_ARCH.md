┌─────────────────────────────────────────────────────────────────────┐
│  USER LAYER:  Production team console / SAP Fiori embedded UI       │
│   - chat, what-if, approval queues, KPI dashboards, Gantt           │
└─────────────────────────────────────────────────────────────────────┘
            │ natural language + structured commands
            ▼
┌─────────────────────────────────────────────────────────────────────┐
│  AGENT ORCHESTRATION LAYER  (LangGraph supervisor + sub-agents)     │
│                                                                     │
│   Supervisor Agent (router/planner)                                 │
│      │                                                              │
│      ├── Constraint-Elicitation Agent (NL → MiniZinc/data row)      │
│      ├── Schedule-Generation Agent (calls solver tool)              │
│      ├── Infeasibility Agent (OptiChat-style, IIS narration)        │
│      ├── What-If Agent (OptiGuide-style, parameter delta + re-solve)│
│      ├── Disruption-Response Agent (ALAS-style, local repair first) │
│      └── Explanation Agent (KPI + plan narration, audit summary)    │
│                                                                     │
│   ──── Verifier-in-the-loop on every agent output ────              │
│        schema check → unit check → solver-feasibility check         │
│        → business-rule check → audit-trail log                      │
└─────────────────────────────────────────────────────────────────────┘
            │ tool calls over MCP / function calling
            ▼
┌─────────────────────────────────────────────────────────────────────┐
│  TOOL / SERVICE LAYER  (each is an MCP server)                      │
│                                                                     │
│  • Solver Service                                                   │
│     - MILP master (lot-sizing, customer-fairness)  → Gurobi/HiGHS   │
│     - CP-SAT subproblem (tank-fill-seal sequencing)                 │
│     - LNS repair engine for reactive replan                         │
│     - declarative model in MiniZinc + Pyomo                         │
│  • Master-Data Service (constraint catalog, BOM, changeover matrix) │
│  • SAP Bridge Service (read Planned Orders, write Process Orders)   │
│  • Real-Time Telemetry Service (Photocells, ProdAction)             │
│  • Discrete-Event Simulator (validate schedule, stress-test)        │
│  • Audit & Versioning Service (every plan + every constraint diff)  │
└─────────────────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────────┐
│  DATA LAYER:  Master data, transactional plans, telemetry, audit    │
│  Hierarchical horizons:  annual → quarterly → weekly → real-time    │
│  Each horizon has its own (aggregated) instance of the same model   │
└─────────────────────────────────────────────────────────────────────┘

---

## Glossary / Abbreviations

| Acronym | Expansion / Meaning |
|---|---|
| **ALAS** | LLM-agent pattern for disruption response — prefers *local repair* (LNS) over full re-solve when telemetry reports a failure. |
| **BOM / BoM** | Bill of Materials — finished-product → semi-finished → API decomposition. |
| **CP-SAT** | Constraint Programming-SATisfiability solver (Google OR-Tools); handles sequencing, no-overlap, sequence-dependent setup. |
| **Fiori** | SAP's design system / UX framework — used here for embedding the production console inside SAP. |
| **Gantt** | Bar-chart visualisation of scheduled tasks against time. |
| **Gurobi / HiGHS** | MILP solvers (commercial / open-source) used for the master problem. |
| **IIS** | Irreducible Infeasible Subsystem — minimal conflicting set of constraints; the Infeasibility Agent narrates this. |
| **KPI** | Key Performance Indicator (utilisation, OTIF, fulfilment rate, etc.). |
| **LangGraph** | LangChain's stateful agent-orchestration framework — drives the Supervisor + sub-agents. |
| **LLM** | Large Language Model. |
| **LNS** | Large Neighborhood Search — metaheuristic for repairing a schedule by destroying and re-optimising a small window. |
| **MCP** | Model Context Protocol — Anthropic's tool/service protocol; every backend service (Solver, Master-Data, SAP Bridge, Telemetry, Simulator, Audit) is exposed as an MCP server. |
| **MILP** | Mixed-Integer Linear Programming. |
| **MiniZinc** | Declarative constraint-modelling language (solver-agnostic). |
| **OptiChat** | LLM-driven pattern for narrating infeasibility (IIS) to users. |
| **OptiGuide** | Microsoft Research pattern for LLM-driven what-if analysis (parameter delta + re-solve + diff narration). |
| **Photocells** | DEMO's real-time monitoring platform that reports pack rate / stoppages from the lines. |
| **ProdAction** | DEMO's batch-recording platform that reports actual produced quantities per batch. |
| **Pyomo** | Python algebraic modelling language for optimisation. |
| **SAP** | The ERP system — Planned Orders are read in, Process Orders are written out. |
| **SAT** | Boolean Satisfiability — the underlying solver class behind CP-SAT. |