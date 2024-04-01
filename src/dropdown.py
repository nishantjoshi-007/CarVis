from dash import dcc

def create_dropdown(id, label, options):
    return dcc.Dropdown(
        id=id,
        options=[{'label': opt, 'value': opt} for opt in options],
        value=options[0]
    )