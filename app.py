import dash
from dash import Dash, dcc, html, Input, Output
import pandas as pd
import plotly.express as px
from src.layout import create_layout

# Load your dataset
df = pd.read_csv('./data/car_price_prediction.csv')

# Initialize the Dash app
app = Dash(__name__)
server = app.server

# Extract necessary data for the layout
manufacturers = df['Manufacturer'].unique()
min_year = df['Prod. year'].min()
max_year = df['Prod. year'].max()
fuel_types = df['Fuel type'].unique()
all_models = df['Model'].unique()

# Set the app layout from the layout module, passing the required data
app.layout = create_layout(manufacturers, min_year, max_year, fuel_types, all_models, multi_manufacturer=True)

@app.callback(
    Output('dynamic-graph', 'figure'),
    [Input('graph-type-dropdown', 'value'),
     Input('manufacturer-dropdown', 'value'),
     Input('prod-year-slider', 'value'),
     Input('model-name-dropdown', 'value'),
     Input('fuel-type-checklist', 'value')]
)
def update_graph(graph_type, selected_manufacturers, selected_years, selected_models, selected_fuel_types):
    filtered_df = df.copy()
    if selected_manufacturers:
        filtered_df = filtered_df[filtered_df['Manufacturer'].isin(selected_manufacturers)]

    filtered_df = filtered_df[
        (filtered_df['Prod. year'] >= selected_years[0]) & 
        (filtered_df['Prod. year'] <= selected_years[1])
    ]

    if selected_models:
        filtered_df = filtered_df[filtered_df['Model'].isin(selected_models)]

    if selected_fuel_types:
        filtered_df = filtered_df[filtered_df['Fuel type'].isin(selected_fuel_types)]
        
    if graph_type == 'Scatter Plot':
        fig = px.scatter(filtered_df, x='Mileage', y='Price', title='Car Price Distribution by Mileage')
    elif graph_type == 'Pie Chart':
        fig = px.pie(filtered_df, names='Manufacturer', title='Car Distribution by Manufacturer')
        fig.update_layout(width=800, height=600)
    elif graph_type == 'Bar Chart':
        fig = px.bar(filtered_df, x='Category', y='Price', title='Average Price by Vehicle Category')
    elif graph_type == 'Box Plot':
        fig = px.box(filtered_df, x='Manufacturer', y='Price', title='Price Distribution by Manufacturer')
    elif graph_type == 'Histogram':
        fig = px.histogram(filtered_df, x='Mileage', title='Mileage Distribution of Vehicles')
    else:
        fig = px.scatter(filtered_df, x='Mileage', y='Price', title='Default: Car Price Distribution by Mileage')

    return fig

@app.callback(
    Output('graph-container', 'style'),
    [Input('graph-type-dropdown', 'value')]
)
def update_graph_container_style(selected_graph_type):
    if selected_graph_type == 'Pie Chart':
        return {'display': 'flex', 'justify-content': 'center'}
    else:
        return {}

# Callback for updating the model dropdown options based on manufacturer selection and search input
@app.callback(
    Output('model-name-dropdown', 'options'),
    [Input('manufacturer-dropdown', 'value'),
     Input('model-name-dropdown', 'search_value')]
)
def update_model_dropdown(selected_manufacturers, search_value):
    if not search_value:
        search_value = ""
    if selected_manufacturers:
        models = df[df['Manufacturer'].isin(selected_manufacturers)]['Model'].unique()
    else:
        models = all_models
    return [{'label': model, 'value': model} for model in models if search_value.lower() in model.lower()]


# Run the app
if __name__ == '__main__':
    app.run_server(debug=True)