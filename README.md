# :star2: CarVis: A Dashboard for Car Decisions-Making
<div align='center'>

<p>Welcome to the 'CarVis' GitHub repository! This project introduces a visualization tool aimed at transforming the car buying and selling process by offering price forecasts and valuable market analyses. With a dataset containing over 19,000 entries and 18 different variables such as car make, model, year of manufacture, and safety features the tool is customized to cater to the needs of users including buyers, car enthusiasts, and dealers. Featuring a user interface the dashboard incorporates elements like Dropdowns, Sliders, Checklists, and Input fields to help users navigate through complex data effortlessly. The main goal is to equip users with insights that support informed decision-making in the automotive sector.</p>
<h4> <a href=https://carvis-dashboard.onrender.com/>Live Demo</a> <span> · </span> <a href="https://github.com/nishantjoshi-007/CarVis/blob/main/Project_Report.pdf"> Documentation </a> <span> · </span> <a href="https://github.com/nishantjoshi-007/Carvis/issues"> Report Bug </a> <span> · </span> <a href="https://github.com/nishantjoshi-007/Carvis/issues"> Request Feature </a> </h4>

</div>

# :notebook_with_decorative_cover: Table of Contents
- [Architecture](#building_construction-architecture)
- [Getting Started](#toolbox-getting-started)
- [Project Docs](#books-project-docs)
- [Contributing](#wave-contributing)
- [License](#warning-license)
- [Acknowledgements](#gem-acknowledgements)

## :building_construction: Architecture

The dashboard reads from **PostgreSQL**, populated by an ETL pipeline that cleans
the source CSV. It falls back to reading the CSV directly when no database is
configured, so it always runs.

```
data/car_price_prediction.csv          19,237 rows, 18 columns
        |
        v   etl/load.py  (extract -> dedupe -> clean -> load -> reconcile)
        |
PostgreSQL star schema                 18,924 listings + 4 dimensions
        |                              + an audit trail of every value changed
        +--> src/data/queries.py  -->  app.py  -->  5 charts
        |
        +--> ml/price_model.py    -->  predicted vs actual price
```

The source data is dirty in ways worth knowing about: every `Doors` value was
corrupted into a date by Excel, 30% of `Levy` is a literal `-`, seven rows carry
`2147483647` km (a 32-bit integer overflow), and 313 rows are exact duplicates.
`etl/clean.py` handles each case and logs every change to a quarantine table.

## :toolbox: Getting Started

### :bangbang: Prerequisites
- [Python 3.11+](https://www.python.org/downloads/)
- [Docker](https://docs.docker.com/engine/install/) — only for the database. The
  dashboard runs without it.

### :running: Run Locally
Clone the project
```bash
git clone https://github.com/nishantjoshi-007/CarVis
cd CarVis
```
Create the environment
```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```
Start everything with one command — it checks the environment, starts
PostgreSQL, loads the data, trains the price model, and serves the app:
```bash
./start-carvis.sh
```

| Flag | Effect |
|---|---|
| `--no-db` | skip PostgreSQL and run on the CSV |
| `--reload` | rebuild the database and retrain the model |
| `--port 8080` | serve on a different port |
| `--stop` | stop the database container |

### :test_tube: Tests and benchmarks
```bash
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/pytest          # 35 tests over the cleaning rules and the model
./.venv/bin/python benchmark.py   # measures what the indexes actually buy
```

## :books: Project Docs
- [`flow.md`](flow.md) — what runs, in what order, and what calls what
- [`decision.md`](decision.md) — why the code is the way it is, and what was
  considered instead
- [`Project_Report.pdf`](Project_Report.pdf) — the original project report

## :wave: Contributing
<img src="https://contrib.rocks/image?repo=Louis3797/awesome-readme-template" /> Contributions to the CarVis are always welcome! Whether it's reporting bugs, suggesting new features, or improving the code, your input is valuable. Please feel free to fork this repository, make your changes, and submit a pull request.

## :warning: License
Distributed under the MIT License. See <a href="https://github.com/nishantjoshi-007/CarVis/blob/main/LICENSE">LICENSE</a> for more information.

## :gem: Acknowledgements
This section is used to mention useful resources and libraries that I have used in your projects.
- [Here is the link to dataset that was used for this project](https://www.kaggle.com/datasets/deepcontractor/car-price-prediction-challenge)
