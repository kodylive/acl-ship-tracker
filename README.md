# ACL Ship Tracker

A small website that shows where every American Cruise Lines ship will be for the next 14 days: which port each ship is in on each day, plus its latest reported position.

- **Web page:** `docs/index.html` (served by GitHub Pages from the `docs` folder)
- **Data:** `docs/data/schedule.json`, written by `scraper/scrape.py`
- **Auto-update:** `.github/workflows/update-schedules.yml` re-runs the scraper every 3 hours and commits any changes. GitHub Pages republishes automatically.

## Where the data comes from

American Cruise Lines' own site blocks automated access, so schedules are read from the public ship pages on [CruiseMapper](https://www.cruisemapper.com/cruise-lines/American-Cruise-Lines-87). For each ship that gives:

- every upcoming cruise (start date, length, departure and arrival port), and
- a day-by-day port list for the ship's **current or next** cruise only.

For later cruises inside the 14-day window, the page shows the departure and arrival days and marks the days in between as "en route" until CruiseMapper publishes the daily stops.

## Arrival times (AIS ETA)

CruiseMapper lists only dates, not times. For times, `scraper/ais_eta.py` listens to live ship broadcasts (AIS) through [aisstream.io](https://aisstream.io) for 8 minutes each run and saves each ship's **next destination and ETA**, as entered by the crew, to `docs/data/eta.json`. The page shows it on each ship's card and on the matching day.

- It covers only the next stop, and only arrival times, not departure times.
- It only appears for ships that are within range of an aisstream receiver during the run, and whose crew has filled in the ETA.
- A report is kept for up to 24 hours if a later run doesn't hear the ship.

To turn it on: sign up at aisstream.io (free, GitHub sign-in works), create an API key, then in your repo go to **Settings → Secrets and variables → Actions → New repository secret**, name it `AISSTREAM_API_KEY`, and paste the key. Without the key, everything else still works.

## Setup on GitHub

1. Create a new repository and push this folder to it.
2. **Settings → Pages:** Source = "Deploy from a branch", Branch = `main`, folder = `/docs`.
3. **Settings → Actions → General → Workflow permissions:** choose "Read and write permissions" (lets the updater commit new data).
4. **Actions tab → Update ship schedules → Run workflow** to do the first refresh by hand.

Your site will be at `https://<your-username>.github.io/<repo-name>/`.

## Run locally

```bash
python scraper/scrape.py
python -m http.server -d docs 8000
```

Then open http://localhost:8000.
