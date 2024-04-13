from dash import html, dcc
from src import slider, checklist

def create_layout(manufacturers, min_year, max_year, fuel_types, all_models, multi_manufacturer=True):
    return html.Div(children=[
        html.H1('CarVis: A Dashboard for Car Decisions-Making',),

        # Manufacturer and Model Name Dropdowns
        html.Div(children=[
            html.Div(children=[
                html.Label('Select Manufacturer:'),
                dcc.Dropdown(
                    id='manufacturer-dropdown',
                    options=[{'label': manufacturer, 'value': manufacturer} for manufacturer in manufacturers],
                    multi=True,
                    placeholder="Select Manufacturers"
                    )
                ], style={'width': '48%', 'display': 'inline-block'}),
            html.Div(children=[
                html.Label('Filter by Model Name:'),
                dcc.Dropdown(
                    id='model-name-dropdown',
                    options=[{'label': model, 'value': model} for model in all_models],
                    placeholder="Enter Model Name",
                    multi=True,
                    search_value='',
                    style={'width': '100%'}
                ),
                html.Div(id='selected-models-display', children=[], style={'margin-top': '10px'})
            ], style={'width': '48%', 'display': 'inline-block', 'float': 'right'}),
        ]),

        # Production Year Range Slider
        html.Div(children=[
            html.Label('Select Production Year Range:'),
            slider.create_slider('prod-year-slider', 'Prod. year', min_year, max_year)
        ], style={'width': '100%', 'padding': '20px 0'}),
        
        # Fuel Type Checklist
        html.Div(children=[
            html.Div(children=[
                checklist.create_checklist('fuel-type-checklist', fuel_types, label="Select Fuel Types:")
            ], style={'width': '48%', 'display': 'inline-block'}),
        ]),
        
        # Container for Histogram and Scatter Plot
        html.Div(children=[
            html.Div(
                dcc.Graph(id='histogram'),
                style={'width': '50%', 'display': 'inline-block'}  # Adjust width as needed
            ),
            html.Div(
                dcc.Graph(id='scatter-plot'),
                style={'width': '50%', 'display': 'inline-block'}  # Adjust width as needed
            )
        ], style={'width': '100%', 'display': 'flex', 'justify-content': 'center'}),

        # Container for Box Plot and Bar Chart
        html.Div(children=[
            html.Div(
                dcc.Graph(id='box-plot'),
                style={'width': '50%', 'display': 'inline-block'}  # Adjust width as needed
            ),
            html.Div(
                dcc.Graph(id='bar-chart'),
                style={'width': '50%', 'display': 'inline-block'}  # Adjust width as needed
            )
        ], style={'width': '100%', 'display': 'flex', 'justify-content': 'center'}),

        # Pie Chart
        html.Div(
            dcc.Graph(id='pie-chart'),
            style={'width': '50%', 'margin': 'auto', 'display': 'block'}  # Adjust width as needed, centered
        )
    ])