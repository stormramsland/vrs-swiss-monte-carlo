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

## Assumptions

The simulation's match win chances rest on the results of my analysis
[vrs-win-rate-analysis](https://github.com/stormramsland/vrs-win-rate-analysis). That
analysis repeats Valve's own check of the VRS model separately for BO1, BO3 and BO5, on
HLTV 1-star matches from 2024 to 2026. From it, this project assumes:

- **Raw VRS win chances are too confident**, so each one is shrunk towards 50% by a
  calibration slope: **0.47 for BO1** and **0.61 for BO3**. BO5 is left out, because 78
  matches were too few to estimate it.
- **Format decides the slope.** By default, advancement and elimination matches are BO3
  and the rest BO1, as in Major Stage 1 and 2.

See [Calibration](#calibration) for the details and how to change these.

> **Not betting advice.** This is a hobby project for exploring how VRS ratings play out in
> a Swiss stage. Every probability comes only from VRS points, a simplified model that
> ignores roster changes, form and map pool. The "value bets" and Kelly
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
cs2-vrs-swiss --bo3 all                # every match best-of-three
cs2-vrs-swiss --calibration 0.8        # one slope for every match (Valve's evaluation)
cs2-vrs-swiss --calibration 1          # raw VRS win chances (no calibration)
```

### Calibration

The raw VRS model is overconfident: favourites win less often than it predicts. The
simulation therefore shrinks every match win chance towards 50%:

    calibrated = 50% + slope × (VRS win chance − 50%)

The slope depends on the series format. It comes from repeating Valve's own model check
separately for BO1 and BO3 on HLTV 1-star matches from 2024 to 2026
([vrs-win-rate-analysis](https://github.com/stormramsland/vrs-win-rate-analysis)):

| Format | Slope | A 90% favourite becomes | A 70% favourite becomes |
|---|---:|---:|---:|
| BO1 | 0.47 | 69% | 59% |
| BO3 | 0.61 | 74% | 62% |
| Valve's single slope (all matches) | 0.8 | 82% | 66% |

Which matches are best-of-three is set with `--bo3`:

- `deciders` (default): matches where a team can advance or be eliminated (2-0, 2-1, 2-2,
  1-2 and 0-2) are BO3, the rest BO1. This is how Major Stage 1 and 2 are played.
- `all`: every match BO3.
- `none`: every match BO1.

Change the slopes with `--calibration-bo1` and `--calibration-bo3`, or set one slope for
every match with `--calibration` (0.8 = Valve's evaluation, 1 = raw model). VRS rating
updates are unaffected; they follow Valve's formula exactly.

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

- Win chances come from VRS points only. The model knows nothing about roster changes or
  form.
- Every match is decided with one random draw. A BO3 match uses the BO3 slope for the
  series result rather than simulating its maps.
- The BO1 and BO3 slopes are estimates from about 600 BO1 and 2,800 BO3 matches, all rated
  1 star or more on HLTV. Lower-tier events could behave differently.
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
