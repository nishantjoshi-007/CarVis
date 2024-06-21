import dash
from dash import Input, Output, callback
import pandas as pd
import plotly.express as px
from src.layout import create_layout

# Load your dataset
df = pd.read_csv('./data/car_price_prediction.csv')

# Initialize the Dash app
app = dash.Dash(__name__)
server = app.server

# Extract necessary data for the layout
manufacturers = df['Manufacturer'].unique()
min_year = df['Prod. year'].min()
max_year = df['Prod. year'].max()
fuel_types = df['Fuel type'].unique()
all_models = df['Model'].unique()

# Set the app layout from the layout module, passing the required data
app.layout = create_layout(manufacturers, min_year, max_year, fuel_types, all_models, multi_manufacturer=True)

# Utility function to filter DataFrame based on user selections
def filter_dataframe(selected_manufacturers, selected_years, selected_models, selected_fuel_types):
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
    
    filtered_df['Mileage'] = filtered_df['Mileage'].str.replace(' km', '').astype(int)
    Q1 = filtered_df['Mileage'].quantile(0.25)
    Q3 = filtered_df['Mileage'].quantile(0.75)
    IQR = Q3 - Q1
    lower_bound = Q1 - 2 * IQR
    upper_bound = Q3 + 2 * IQR
    filtered_df = filtered_df[(filtered_df['Mileage'] >= lower_bound) & (filtered_df['Mileage'] <= upper_bound)]
    return filtered_df

@app.callback(
    Output('scatter-plot', 'figure'),
    [Input('manufacturer-dropdown', 'value'),
     Input('prod-year-slider', 'value'),
     Input('model-name-dropdown', 'value'),
     Input('fuel-type-checklist', 'value')]
)
def update_scatter_plot(selected_manufacturers, selected_years, selected_models, selected_fuel_types):
    filtered_df = filter_dataframe(selected_manufacturers, selected_years, selected_models, selected_fuel_types)
    fig = px.scatter(filtered_df, x='Mileage', y='Price', title='Car Price Distribution by Mileage')
    return fig

@app.callback(
    Output('pie-chart', 'figure'),
    [Input('manufacturer-dropdown', 'value'),
     Input('prod-year-slider', 'value'),
     Input('model-name-dropdown', 'value'),
     Input('fuel-type-checklist', 'value')]
)
def update_pie_chart(selected_manufacturers, selected_years, selected_models, selected_fuel_types):
    filtered_df = filter_dataframe(selected_manufacturers, selected_years, selected_models, selected_fuel_types)
    fig = px.pie(filtered_df, names='Manufacturer', title='Car Distribution by Manufacturer')
    fig.update_layout(height=600, width=800)
    return fig

@app.callback(
    Output('bar-chart', 'figure'),
    [Input('manufacturer-dropdown', 'value'),
     Input('prod-year-slider', 'value'),
     Input('model-name-dropdown', 'value'),
     Input('fuel-type-checklist', 'value')]
)
def update_bar_chart(selected_manufacturers, selected_years, selected_models, selected_fuel_types):
    filtered_df = filter_dataframe(selected_manufacturers, selected_years, selected_models, selected_fuel_types)
    avg_price_per_category = filtered_df.groupby('Category')['Price'].mean().reset_index()
    fig = px.bar(avg_price_per_category, x='Category', y='Price', color='Category', title='Average Price by Vehicle Category')
    return fig

@app.callback(
    Output('box-plot', 'figure'),
    [Input('manufacturer-dropdown', 'value'),
     Input('prod-year-slider', 'value'),
     Input('model-name-dropdown', 'value'),
     Input('fuel-type-checklist', 'value')]
)
def update_box_plot(selected_manufacturers, selected_years, selected_models, selected_fuel_types):
    filtered_df = filter_dataframe(selected_manufacturers, selected_years, selected_models, selected_fuel_types)
    fig = px.box(filtered_df, x='Manufacturer', y='Price', title='Price Distribution by Manufacturer')
    return fig

@app.callback(
    Output('histogram', 'figure'),
    [Input('manufacturer-dropdown', 'value'),
     Input('prod-year-slider', 'value'),
     Input('model-name-dropdown', 'value'),
     Input('fuel-type-checklist', 'value')]
)
def update_histogram(selected_manufacturers, selected_years, selected_models, selected_fuel_types):
    filtered_df = filter_dataframe(selected_manufacturers, selected_years, selected_models, selected_fuel_types)
    fig = px.histogram(filtered_df,  x='Mileage', title='Mileage Distribution of Vehicles')
    return fig

# Run the app
if __name__ == '__main__':
    app.run_server(debug=False)
