from dash import html, dcc
from src import dropdown, slider, input, checklist

def create_layout(manufacturers, min_year, max_year, fuel_types, all_models, multi_manufacturer=True):
    graph_options = ['Scatter Plot', 'Pie Chart', 'Bar Chart', 'Box Plot', 'Histogram']

    return html.Div(children=[
        html.H1('Car Price Prediction Dashboard',),

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

        html.Div(children=[
            html.Label('Select Production Year Range:'),
            slider.create_slider('prod-year-slider', 'Prod. year', min_year, max_year)
        ], style={'width': '100%', 'padding': '20px 0'}),

        html.Div(children=[
            html.Div(children=[
                checklist.create_checklist('fuel-type-checklist', fuel_types, label="Select Fuel Types:")
            ], style={'width': '48%', 'display': 'inline-block'}),

            html.Div(children=[
                html.Label('Select Graph Type:'),
                dcc.Dropdown(
                    id='graph-type-dropdown',
                    options=[{'label': graph_type, 'value': graph_type} for graph_type in graph_options],
                    value='Scatter Plot'
                )
            ], style={'width': '48%', 'display': 'inline-block', 'float': 'right'}),
        ]),

        html.Div(
            dcc.Graph(id='dynamic-graph'),
            id='graph-container'
        )
    ])