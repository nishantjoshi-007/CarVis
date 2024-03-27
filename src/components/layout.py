from dash import Dash, html

def create_layout(app: Dash) -> html.Div:
    return html.Div(
        className='container', 
        children=[
            html.H1(app.title),
            #html.Div('Dash: Web Dashboards with Python'),
            html.Hr('Hi'),]
        )