# iron_fly_strategy

Python package for **Dale's** SPX 0DTE iron fly rules: YAML desk config, fly book, and add/drop decisions.

Inspired by [Dale's iron fly on YouTube](https://www.youtube.com/watch?v=kNR2r8Nj3qI).

## Install

From Git (after the first tagged release):

```bash
pip install "iron-fly-strategy @ git+https://github.com/askosyrskiy/iron_fly_strategy.git@v0.1.0"
```

Editable checkout:

```bash
git clone https://github.com/askosyrskiy/iron_fly_strategy.git
cd iron_fly_strategy
python3.12 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Requires Python 3.12+.

## Quick use

```python
from iron_fly_strategy import DaleOg
from iron_fly_strategy.spec import parse_spec, DEFAULT_SPEC
from iron_fly_strategy.book import FlyBook

spec = parse_spec(DEFAULT_SPEC)
book = FlyBook()
strategy = DaleOg(book)
```

## Package layout

| Module | Role |
|---|---|
| `dale_og.py` | Strategy engine: `on_quote` / `pending_intents` — when to add or close flies |
| `book.py` | Open-fly book and Captain's Log ladder (strikes, credit, watch levels) |
| `spec.py` | One-day YAML: `DeskSpec`, `parse_spec`, `DEFAULT_SPEC`, clocks and auto fields |
| `config_state.py` | Apply picks, cookies, follow-wings, and EM gates to the live book state |
| `rules.py` | Regime grids, stop pads, wing clocks, scale modes, shared labels |
| `view.py` | Lightweight view models for ladder / strip display data |
| `models.py` | Shared types: `Quote`, `Intent`, `IntentKind`, `SessionMode` |

## Dev

```bash
make lint
make format
make test
```
