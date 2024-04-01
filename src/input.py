from dash import dcc, html

def create_input(id, placeholder="Enter text"):
    return html.Div([
        dcc.Input(id=id, type='text', placeholder=placeholder),
    ])