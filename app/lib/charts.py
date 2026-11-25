"""Shared Plotly styling and color palette (adapts to Light & Dark mode)."""

from __future__ import annotations

import plotly.graph_objects as go

# Consistent palette across all charts
GENDER_COLORS: dict[str, str] = {
    "Men": "#38BDF8",
    "Women": "#F472B6",
    "Other / Mixed": "#94A3B8",
}

FORMAT_COLORS: dict[str, str] = {
    "Best of 5": "#34D399",
    "Best of 7": "#A78BFA",
    "Other": "#94A3B8",
}


def style_figure(fig: go.Figure, *, height: int = 360) -> go.Figure:
    """Apply clean layout settings that adapt to Streamlit's Light/Dark theme."""
    fig.update_layout(
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"size": 13},
        title={"x": 0.01, "xanchor": "left", "font": {"size": 15}},
        legend={
            "bgcolor": "rgba(0,0,0,0)",
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "right",
            "x": 1.0,
        },
        margin={"l": 45, "r": 25, "t": 60, "b": 40},
    )
    fig.update_xaxes(
        gridcolor="rgba(128, 128, 128, 0.2)",
        zerolinecolor="rgba(128, 128, 128, 0.2)",
    )
    fig.update_yaxes(
        gridcolor="rgba(128, 128, 128, 0.2)",
        zerolinecolor="rgba(128, 128, 128, 0.2)",
    )
    return fig
