from dash import Dash, html
from dash_bootstrap_components.themes import BOOTSTRAP
from src.components.layout import create_layout

def main() -> None:
    app = Dash(__name__, external_stylesheets=[BOOTSTRAP])
    app.title = 'Project 1'
    app.layout = create_layout(app)
    app.run()
    
if __name__ == '__main__':
    main()