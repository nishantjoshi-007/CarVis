# Fuel-type checklist. Used by src/ui/layout.py.

from dash import dcc, html


def create_checklist(id, options, label="Select options"):
    return html.Div(children=[
        html.Label(label),
        dcc.Checklist(
            id=id,
            options=[{'label': i, 'value': i} for i in options],
            # Pre-select the first option so the dashboard opens with data
            # rather than five empty charts.
            value=[options[0]],
        )
    ])
