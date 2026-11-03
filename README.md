# Election Forest

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/Om5001/ElectionMap?quickstart=1)
<!-- After pushing, replace Om5001/ElectionMap above with your GitHub username and repo name. -->

A live forecast of the 2026 **Senate**, **House** and **Governor** races. The core is a random forest whose factor weights are bootstrapped from each factor's correlation with results. The forecast is then blended with prediction markets and with vote counts as they come in.

The site has three panels:

| Panel | What it shows |
|---|---|
| **The forest at work** | 400 trees voting on the selected race (animated each time the forest is re-grown), a step-by-step breakdown of how the forecast is built, the bootstrapped factor-weight distributions, sample trees with the factors each one drew, a 2010–2022 backtest, the live re-weighting layer, and the methodology. |
| **Map** | A Senate / House / Governor toggle. Colours show the probability for each race. Counting and called races are marked. Chamber-control probabilities come from 20,000 correlated simulations. The House tab also has a grid of every modeled district. |
| **Key races** | The 10–15 closest and most-watched races, chosen automatically and anchored by a pinned list. Each card shows the probability, a sparkline of how it has moved, poll, market and forest readings, a flag when markets disagree, and the percentage of the vote counted. |

---

## What to put on GitHub (step by step)

1. **Create the repo.** On GitHub, go to New repository, name it (e.g. `election-forest`), make it Public, and don't add a README.
2. **Push this folder:**
   ```bash
   cd election-forest
   git remote add origin https://github.com/<you>/election-forest.git
   git push -u origin main
   ```
3. **Turn on Pages.** In the repo, go to **Settings → Pages → Build and deployment → Source** and choose **GitHub Actions**.
4. **Add optional secrets** under **Settings → Secrets and variables → Actions → Secrets**:
   | Secret | Why | Where to get it |
   |---|---|---|
   | `FRED_API_KEY` | Higher limits on economic data. It works without a key, using FRED's public CSV. | Free at fred.stlouisfed.org → My Account → API Keys |
   | `AP_API_KEY` | Official AP vote counts and race calls for all 435 House seats. This is the best election-night feed. | Paid: AP Elections API (developer.ap.org) |
5. **Add optional variables** on the same page, under the **Variables** tab:
   | Variable | Example | Use |
   |---|---|---|
   | `GENERIC_BALLOT_D_MARGIN` | `8.7` | The national environment input. Update it from the Silver Bulletin or RealClearPolling average. |
   | `POLLS_CSV_URL` | Published Google Sheet CSV | Extra polls with the same columns as `data/manual/polls.csv` |
   | `RESULTS_CSV_URL` | Published Google Sheet CSV | Hand-entered counts on election night (columns of `data/manual/results.csv`) |
   | `RESULTS_JSON_URL` | Your own endpoint | Any results feed you build (format in `forecast/data/results.py`) |
   | `EARLY_VOTE_CSV_URL` | Published Google Sheet CSV | Absentee and early-vote totals by state (columns of `data/manual/early_vote.csv`) |
6. **Run it.** Go to **Actions → Forecast → Run workflow**. The site goes live at `https://<you>.github.io/election-forest/`.

After that it runs on its own:

- every 20 minutes on normal days;
- **every 5 minutes on election night** (Nov 3, 5 pm ET through 9 am ET Nov 4);
- every 10 minutes during late counting (Nov 5–7).

GitHub can delay scheduled runs on busy nights. For true real-time updates, run the local live server (below) on your own machine.

### Election night checklist
- [ ] If you have AP access, add `AP_API_KEY`. That covers everything.
- [ ] Without AP, list state results pages in `data/manual/clarity_sources.csv` (columns `state,url`). Many states publish live `summary.json` at `results.enr.clarityelections.com/<ST>/<election-id>/<version>/json/en/summary.json`.
- [ ] Or keep a Google Sheet with columns `race,dem_votes,rep_votes,other_votes,pct_in,called,winner`, publish it as CSV, and set it as `RESULTS_CSV_URL`. Race keys look like `senate:TX`, `senate:OH:special`, `governor:GA` and `house:PA-07`.
- [ ] Run `python scripts/live_server.py --every 60` locally for minute-by-minute updates.

---

## Data feeds

| Category | Source | Key? | Code |
|---|---|---|---|
| Prediction markets | Kalshi, Polymarket, PredictIt, Manifold (weights ×1, ×1, ×0.75, ×0.5) | No | `pm/sources/` |
| Polls (2026) | Seeded final polls with source links (`data/manual/polls.csv`), Wikipedia polling tables for every Senate and Governor race, and your CSV | No | `forecast/data/polls.py` |
| Polls (history) | FiveThirtyEight polling archive: polls in the final weeks plus certified results, 1998–2022 (CC BY 4.0) | Bundled | `data/reference/538_raw_polls.csv` |
| Pollster quality | 538 pollster ratings | Bundled | `data/reference/538_pollster_ratings.csv` |
| Economy and commodities | FRED: CPI, WTI crude, unemployment, gasoline, consumer sentiment and 10-year yield, with a bundled CPI and WTI fallback | Optional | `forecast/data/macro.py` |
| Partisan lean | County presidential returns 2008–2024, aggregated to states | Bundled | `data/reference/pres_by_state.csv` |
| District lean | MIT Election Lab House returns (fetched by the Action) | No | `scripts/fetch_district_lean.py` |
| Early vote and turnout | Your CSV or sheet, using Secretary of State or UF Election Lab figures | No | `forecast/data/early_vote.py` |
| Live results | AP API, Clarity ENR, your JSON or CSV, and market prices at 99¢ or more | AP only | `forecast/data/results.py` |

Every feed is optional and fails safely. The header's **Data feeds** list shows what is live, what fell back and what is offline.

---

## Methodology

**Layer 1: correlation-weighted bootstrap forest** (`forecast/model/forest.py`)

- **Training set.** 838 polled Senate, Governor and House races, 2010–2022, with the certified result as the target (Democratic minus Republican margin).
- **Factors.** Poll average, poll volume, pollster disagreement, pollster quality, state partisan lean, generic ballot, midterm penalty, CPI inflation, oil price and unemployment. The economic factors are signed against the White House party. Office type is also a factor.
- **How each tree is built.** Each tree draws a bootstrap sample of races. On that sample it computes |Spearman ρ| between each factor and the outcome. Factors that change only once per election (the economy, the generic ballot) are discounted by the number of elections, not the number of races. The tree then samples its factors with probability proportional to those correlations and grows a regression tree.
- **Why the weights have ranges.** The weights are re-estimated on every bootstrap sample, so the site shows their full distribution.
- **Anchor plus corrections.** The forest predicts corrections to an anchor, which is the poll average, or state lean plus the national environment when there are no polls. How much of the correction to trust is chosen by leave-one-cycle-out cross-validation.

**Backtest** (leave-one-cycle-out, 838 races):

| Model | RMSE (pts) | Winner correct | Brier |
|---|---|---|---|
| **Correlation-weighted forest** | **6.99** | **86.6%** | **0.096** |
| Poll average only | 7.17 | 85.4% | 0.099 |
| Standard random forest | 7.78 | 85.3% | 0.104 |

**Uncertainty.** Errors are split into a shared national error (σ ≈ 3.8 pts, the "polls missed everywhere" effect) and a race-specific error. Seat totals come from 20,000 correlated simulations.

**Layer 2: live ensemble.** The forest is blended with the market consensus. The blend starts at a prior of 55/45. As races are decided, the blend is re-estimated by bootstrapping over the decided races, using inverse squared error. The average miss on decided races is applied as a national shift to races still counting. Early-vote party swing adds a small nudge.

**Layer 3: counting.** Margin = r·counted + (1−r)·forecast, with r = (share of expected vote in)^1.5. Calls from AP or from your manual entries override the model.

**Limits.**

- The economic factors have only 7 elections of history, so they rarely earn much weight. That is shown honestly rather than forced.
- Market and early-vote data have no history in the training set, so they enter at layer 2.
- Without the district-lean file, House districts that have no polls use state lean.

---

## Open in a Codespace

**Getting the files into a new, empty repo.** Do this once.

1. Create a public repo on GitHub with "Add a README file" ticked.
2. Click **Code → Codespaces → Create codespace on main**.
3. Drag the downloaded zip (any name, e.g. `election-forest (1).zip`) onto the Explorer panel on the left.
4. Paste into the terminal:
   ```bash
   Z=$(ls -t *.zip | head -1); echo "Using: $Z"
   find . -mindepth 1 -maxdepth 1 ! -name .git ! -name "$Z" -exec rm -rf {} +
   rm -rf /tmp/ef && unzip -q "$Z" -d /tmp/ef && rm -rf /tmp/ef/election-forest/.git
   cp -r /tmp/ef/election-forest/. . && rm -rf /tmp/ef "$Z"
   bash scripts/codespace_setup.sh
   ```
   This installs the packages, checks the folder layout, runs the tests, and commits and pushes.
5. Start the site: `python3 scripts/live_server.py --every 90`. It opens on port 8000.

There is no need to rebuild the container. Any Codespace you create from now on sets itself up automatically.

**Day to day:** click the badge at the top, or **Code → Codespaces**. **Terminal → Run Task…** lists shortcuts. Keys for use inside a Codespace go under **Settings → Secrets and variables → Codespaces**. Codespaces has open internet, so it is the quickest way to check that every feed connects.

## Enrichment data (2024 results, 2026 district lines, demographics)

```bash
python scripts/fetch_enrichment.py --backtest
```

This downloads four sources, then re-runs the backtest and prints the scores before and after:

| Source | What it adds | File written |
|---|---|---|
| MIT Election Lab, certified state presidential returns | Fixes the incomplete 2012/2016 county sums behind state lean | `data/reference/pres_by_state.csv` (original kept as `pres_by_state_countysum.csv`) |
| The Downballot, 2024 presidential results on the **2026** district lines | Correct lean for the 10 states that redrew (TX, MO, NC, OH, CA, UT, AL, FL, LA, TN) | `data/reference/house_district_lean.csv` |
| Silver Bulletin raw polls (Feb 2025) | Adds the **2024** cycle to training, an 8th election | `data/reference/sb_raw_polls.csv` |
| Census ACS 5-year | College %, Hispanic %, Black %, median age and income, for states and districts | `data/reference/acs_state.csv`, `acs_cd.csv` |

Every source is optional, and the model runs without any of them. If an automatic download fails (sites change), save the file by hand:

- **Downballot:** open their Google Sheet, choose File → Download → CSV, and save it as `data/manual/downballot_2026_districts.csv`. Or set `DOWNBALLOT_CSV_URL`.
- **Silver Bulletin:** download "Rawpolls" from natesilver.net/p/pollster-ratings-archive-2025 and save it as `data/manual/silver_bulletin_raw_polls.xlsx`. Or set `SB_RAW_POLLS_URL`.
- **MIT:** save the president file from Harvard Dataverse (doi:10.7910/DVN/42MVDX) as `data/manual/medsl_president.csv`.
- **Census:** works without a key. A free `CENSUS_API_KEY` lifts the rate limits.

Commit the new `data/reference/*.csv` files so the GitHub Action uses them too. If you publish, credit MIT Election Data + Science Lab, The Downballot, Silver Bulletin and the U.S. Census Bureau.

## Run locally

```bash
pip install -r requirements.txt
python -m forecast.pipeline              # live run -> site/data/*.json
python -m http.server -d site 8000       # open http://localhost:8000
python scripts/live_server.py            # both, looping every 90 s

python -m forecast.pipeline --demo                       # synthetic market prices (labelled)
python -m forecast.pipeline --demo --simulate-night 50   # + simulated counts, to test election-night mode
python -m forecast.model.train           # backtest and weight table in the terminal
pytest -q
```

The `site/data` folder in this repo is a **demo build**: the market prices are synthetic, while the polls, economic data and model are real. The first Action run replaces it with live data.

## Layout
```
forecast/
  config.py            race calendar, Senate math, pinned races, priors
  features.py          factor engineering (shared by history and 2026)
  data/                history, polls, macro, markets, early_vote, results adapters
  model/forest.py      correlation-weighted bootstrap forest
  model/election_model.py  anchor + forest corrections
  model/train.py       leave-one-cycle-out CV, error scales
  live.py              ensemble re-weighting, count blending, seat simulation
  pipeline.py          orchestration -> site/data/{forecast,model,history}.json
pm/                    prediction-market clients (Kalshi, Polymarket, PredictIt, Manifold)
site/                  static three-panel front end (d3 and topojson vendored)
scripts/               live_server.py, fetch_district_lean.py
.github/workflows/     forecast.yml (schedule plus election night), tests.yml
```

## Licenses and credits
Code: MIT. Historical polls and pollster ratings: FiveThirtyEight (CC BY 4.0). County presidential returns: tonmcg/US_County_Level_Election_Results_08-24. CPI and WTI: datasets/cpi-us and datasets/oil-prices (from BLS and EIA). Economic data: FRED, Federal Reserve Bank of St. Louis. House returns: MIT Election Data and Science Lab. Market data belongs to its respective platforms; check each platform's terms before any commercial use. Not financial advice.
