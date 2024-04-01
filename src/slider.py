from dash import dcc

def create_slider(id, label, min_value, max_value, step=1):
    return dcc.RangeSlider(
        id=id,
        min=min_value,
        max=max_value,
        step=step,
        value=[min_value, max_value],
        marks={i: str(i) for i in range(min_value, max_value+1, step*5)}
    )