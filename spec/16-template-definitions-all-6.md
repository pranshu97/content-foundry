## 16. Template Definitions (All 6)

Templates are the engine of "systematic variation": identical grounding, deliberately different structure. Each is a `Template` dataclass (`templates/definitions.py`) registered in `TEMPLATES`.

### 16.1 `Template` shape
```python
@dataclass(frozen=True)
class Template:
    id: str
    name: str
    when_to_use: str
    beats: list[str]            # ordered beat sheet injected into the generator prompt
    default_perspective: str    # e.g. "second-person, present-tense"
```

### 16.2 The six templates
| id | Name | When to use | Beat sheet (ordered) |
|----|------|-------------|----------------------|
| `problem_solution` | **The Problem/Solution Model** | A painful, common career problem with a data-backed fix | 1) Name the painful problem 2) Why the obvious fix fails 3) The data 4) The real solution 5) First step today |
| `myth_vs_reality` | **Myth vs. Reality** | A widely-believed career "truth" the data contradicts | 1) State the myth 2) Why people believe it 3) The contradicting data 4) The reality 5) What to do instead |
| `three_step` | **The 3-Step Strategy** | An achievable goal that fits a clean 3-move plan | 1) The outcome + stakes 2) Step 1 3) Step 2 4) Step 3 5) Recap + CTA |
| `contrarian` | **The Contrarian Take** | Conventional advice that is now wrong given the data | 1) The popular advice 2) "Here's why that's now backwards" 3) Evidence 4) The contrarian play 5) Caveats + action |
| `case_study` | **The Case Study / Story** | A concrete example/persona illustrating a trend | 1) Meet the situation 2) The turning point 3) What the data shows broadly 4) The lesson 5) Apply it to you |
| `data_deep_dive` | **The Data Deep-Dive** | A striking dataset worth unpacking | 1) The surprising number 2) Break it down 3) What's driving it 4) What it means for you 5) The move to make |

### 16.3 Selection & anti-fatigue logic
- The orchestrator reads `template_usage` for the last `FATIGUE_LOOKBACK` runs and picks the **least-recently-used** template (weighted-random among the bottom half) for variety.
- On a Judge `force_shift`, the current template is excluded and a **perspective modifier** is layered on (e.g., switch to second-person, future-tense, or a skeptical lens) so even a reused template feels structurally fresh.
- `default_perspective` seeds tone; the modifier overrides it on forced shifts.

### 16.4 Extensibility
Adding a 7th template = appending one `Template` to `TEMPLATES`. No other code changes; selection, fatigue tracking, and prompting pick it up automatically.

---

---
[← Index](README.md) · [← Prev](15-prompt-library.md) · [Next →](17-cli-interface.md)
