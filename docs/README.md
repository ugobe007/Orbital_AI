# Orbital AI docs

Authoritative engineering references for the **robot-control abstraction layer**
(Orbital AI Cloud + ARIA edge + fleet adapters).

| Document | Purpose |
|----------|---------|
| [PHASES_AND_STATUS.md](./PHASES_AND_STATUS.md) | **Partner review** — phases, completed work, next decisions |
| [ARIA_ORBITAL_AI_BUILD_GUIDE.md](./ARIA_ORBITAL_AI_BUILD_GUIDE.md) | Architecture, modules 1–7, security, sprint backlog |
| [ARIA_ORBITAL_AI_Presentation_Script.md](./ARIA_ORBITAL_AI_Presentation_Script.md) | Presentation narrative |
| [GAP_ANALYSIS.md](./GAP_ANALYSIS.md) | Desktop repo vs build guide (simulation lens) |
| [SPRINT_PRIORITIZATION.md](./SPRINT_PRIORITIZATION.md) | Abstraction-first engineering order |
| [HARDWARE_GATE.md](./HARDWARE_GATE.md) | Sprint D lab checklist (ArUco, Unitree, VLAN/SROS2/audit) |
| [OEM_API_CONNECTIONS.md](./OEM_API_CONNECTIONS.md) | Public OEM API research + dry-run clients (`fleet_adapters/oem_apis/`) |
| [OEM_API_SECURITY.md](./OEM_API_SECURITY.md) | OEM API inventory gaps + defense-in-depth security phases |

**Repos:** `/Users/robertchristopher/Desktop/Orbital_AI` (Python sim + Fly).  
**Client apps** (StageGate, ReadyForRobots) consume Orbital over API/embed — they are not this repo.
