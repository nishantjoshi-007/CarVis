# Production-year range slider. Used by src/ui/layout.py.

from dash import dcc


def create_slider(id, label, min_value, max_value, step=1):
    return dcc.RangeSlider(
        id=id,
        min=min_value,
        max=max_value,
        step=step,
        value=[min_value, max_value],
        # Label every 5th step: one mark per year would be unreadable over
        # the 1939-2020 range in the source data.
        marks={i: str(i) for i in range(min_value, max_value+1, step*5)}
    )
