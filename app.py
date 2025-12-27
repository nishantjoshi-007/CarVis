"""
CarVis — interactive dashboard for used-car market data.

Entry point. Run with:

    gunicorn app:server          # production (Render, and locally)
    python app.py                # local development

Each of the five charts asks src/queries.py for exactly the data it needs.
Those queries run against PostgreSQL when DATABASE_URL is set and the database
answers, and fall back to reading the CSV when it is not -- so the dashboard
stays up whatever the database is doing. See decision.md (D6) and flow.md.
"""

import dash
import plotly.express as px
from dash import Input, Output

from ml import price_model
from src import db, queries
from src.layout import create_layout

app = dash.Dash(__name__)
server = app.server

app.title = "CarVis"
app._favicon = "icon.ico"

# Read once at start-up, from the dimension tables when the database is up.
# Note what is NOT here any more: the whole dataset. It used to be loaded into
# a module-level DataFrame, which meant every gunicorn worker process carried
# its own full copy in memory.
options = queries.get_filter_options()
# flush=True: gunicorn's workers do not run on a tty, so Python block-buffers
# stdout and this line would not appear until the buffer filled -- i.e. never.
print(f"[carvis] data source: {'postgres' if db.is_available() else 'csv fallback'}",
      flush=True)

app.layout = create_layout(
    options["manufacturers"],
    options["min_year"],
    options["max_year"],
    options["fuel_types"],
    options["models"],
    multi_manufacturer=True,
)

# All five callbacks take the same four control values.
FILTER_INPUTS = [
    Input("manufacturer-dropdown", "value"),
    Input("prod-year-slider", "value"),
    Input("model-name-dropdown", "value"),
    Input("fuel-type-checklist", "value"),
]

def _message_figure(text):
    fig = px.scatter(x=[], y=[])
    fig.add_annotation(text=text, showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5)
    fig.update_layout(xaxis={"visible": False}, yaxis={"visible": False})
    return fig


LABELS = {
    "mileage_km": "Mileage (km)",
    "price_usd": "Price (USD)",
    "avg_price": "Average price (USD)",
    "predicted_usd": "Predicted price (USD)",
    "manufacturer_name": "Manufacturer",
    "category_name": "Category",
    "listings": "Listings",
}


@app.callback(Output("scatter-plot", "figure"), FILTER_INPUTS)
def update_scatter_plot(manufacturers, years, models, fuel_types):
    df = queries.scatter_mileage_price(manufacturers, years, models, fuel_types)
    return px.scatter(df, x="mileage_km", y="price_usd", labels=LABELS,
                      title="Car Price Distribution by Mileage")


@app.callback(Output("pie-chart", "figure"), FILTER_INPUTS)
def update_pie_chart(manufacturers, years, models, fuel_types):
    # Aggregated server-side: one row per manufacturer, not one per listing.
    df = queries.count_by_manufacturer(manufacturers, years, models, fuel_types)
    fig = px.pie(df, names="manufacturer_name", values="listings",
                 title="Car Distribution by Manufacturer")
    fig.update_layout(height=600, width=800)
    return fig


@app.callback(Output("bar-chart", "figure"), FILTER_INPUTS)
def update_bar_chart(manufacturers, years, models, fuel_types):
    # GROUP BY / AVG in the database: 11 rows cross the wire, not ~18,900.
    df = queries.avg_price_by_category(manufacturers, years, models, fuel_types)
    return px.bar(df, x="category_name", y="avg_price", color="category_name",
                  labels=LABELS, title="Average Price by Vehicle Category")


@app.callback(Output("box-plot", "figure"), FILTER_INPUTS)
def update_box_plot(manufacturers, years, models, fuel_types):
    df = queries.price_by_manufacturer(manufacturers, years, models, fuel_types)
    return px.box(df, x="manufacturer_name", y="price_usd", labels=LABELS,
                  title="Price Distribution by Manufacturer")


@app.callback(Output("prediction-plot", "figure"), FILTER_INPUTS)
def update_prediction_plot(manufacturers, years, models, fuel_types):
    df = queries.price_prediction_frame(manufacturers, years, models, fuel_types)
    predicted = price_model.predict(df)
    if predicted is None:
        return _message_figure("Price model unavailable — run: python -m ml.price_model")

    df = df.assign(predicted_usd=predicted)
    fig = px.scatter(df, x="price_usd", y="predicted_usd", labels=LABELS,
                     title="Predicted vs Actual Price")
    # A perfect model would put every point on this line.
    limit = float(max(df["price_usd"].max(), df["predicted_usd"].max()))
    fig.add_shape(type="line", x0=0, y0=0, x1=limit, y1=limit,
                  line=dict(dash="dash", width=1))
    return fig


@app.callback(Output("histogram", "figure"), FILTER_INPUTS)
def update_histogram(manufacturers, years, models, fuel_types):
    df = queries.mileage_distribution(manufacturers, years, models, fuel_types)
    return px.histogram(df, x="mileage_km", labels=LABELS,
                        title="Mileage Distribution of Vehicles")


if __name__ == "__main__":
    app.run(debug=False)
