# cs2-vrs-swiss-monte-carlo

Tools built on CS2's Valve Regional Standings (VRS):

- **VRS rating update** for a single match: a Python port of the Glicko/Elo step in Valve's
  standings model, checked to give identical results to the original JavaScript.
- **Swiss stage** (16 teams, 3 wins advance, 3 losses eliminated): later rounds are drawn
  by record with no rematches.
- **Monte Carlo**: each team's chance to qualify, plus the qualifiers of every simulated run.
- **Value bets**: bets whose EV beats the bookmaker's margin, sized together with Kelly
  across the simulated runs.

No dependencies outside the Python standard library (3.10+).

> **Not betting advice.** This is a hobby project for exploring how VRS ratings play out in
> a Swiss stage. Every probability comes only from VRS points, a simplified model that
> ignores roster changes, form, map pool and best-of-3 series. The "value bets" and Kelly
> stakes it prints are the output of that model, not recommendations. Where the model
> disagrees strongly with the bookmakers, the model is more likely to be wrong. Gambling
> involves risk of loss; only bet what you can afford to lose, and check that betting is
> legal where you live.

> **Built with AI.** The code, tests and documentation in this project were written by
> [Claude Code](https://claude.com/claude-code) (Anthropic's Claude), working from my
> instructions: I chose what to build, supplied the data (VRS points, matchups, odds) and
> the rules, and reviewed the results. The VRS rating update is ported from Valve's
> open-source standings model and was checked against it.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Project layout

```
data/                 inputs you edit
  vrs_points.csv      team,points
  round1.csv          team1,team2         (round-1 matchups)
  odds.csv            team,qualify,not_qualify
output/               generated (not committed)
  montecarlo_results.csv
  montecarlo_runs.csv one row per run, 1 = qualified
  bets.csv            bets to place
src/cs2_vrs_swiss/
  vrs_match.py        VRS rating update after a match
  swiss.py            Swiss stage rules and simulation
  montecarlo.py       Monte Carlo over the Swiss stage
  kelly.py            Kelly criterion, odds conversion
  bets.py             value bets + joint Kelly sizing
  pipeline.py         everything in one command (cs2-vrs-swiss)
tests/
```

Team names must be spelled the same in all three input files.

## Usage

Run from the project root.

```bash
cs2-vrs-swiss                          # simulate + find bets (50€ bankroll, 5€ max bet, half Kelly)
cs2-vrs-swiss --bankroll 25
cs2-vrs-swiss --skip-sim               # only the odds changed: reuse the last simulation
cs2-vrs-swiss --fraction 1 --seed 1    # full Kelly, repeatable
```

Individual tools:

```bash
python -m cs2_vrs_swiss.vrs_match 1573 1414           # ratings after PVISION beats DENDELE
python -m cs2_vrs_swiss.swiss data/vrs_points.csv     # play one stage, print every match
python -m cs2_vrs_swiss.montecarlo data/vrs_points.csv data/round1.csv
python -m cs2_vrs_swiss.kelly 0.6 2.10                # Kelly stake for one bet
python -m cs2_vrs_swiss.bets --fraction 1             # full Kelly
```

## Tests

```bash
python -m unittest discover -s tests
```

## Limitations

- Win chances come from VRS points only, and every match is decided as a single game
  (no best-of-3). The model knows nothing about roster changes or form.
- Simulations always start from 0-0; results already played can't be entered yet.
- A one-match VRS update is not the next published standings: Valve recalculates every
  team's starting points and drops old matches each time.

## Credits and license

The VRS rating update in `src/cs2_vrs_swiss/vrs_match.py` is a Python port of the Glicko
step (`glicko.js`, `ranking_context.js`) from Valve's
[counter-strike_regional_standings](https://github.com/ValveSoftware/counter-strike_regional_standings)
repository, which Valve published for people to inspect and build on. That repository has
no license file, so the ported logic remains Valve's; the MIT license below covers the
rest of this project.

Licensed under the [MIT License](LICENSE).
