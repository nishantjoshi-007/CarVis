from dash import dcc, html

def create_checklist(id, options, label="Select options"):
    return html.Div(children=[
        html.Label(label),
        dcc.Checklist(
            id=id,
            options=[{'label': i, 'value': i} for i in options],
            value=[options[0]],
        )
    ])