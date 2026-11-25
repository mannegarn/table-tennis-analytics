"""Shared Plotly styling, palettes, and mobile-friendly chart rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import plotly.graph_objects as go  # type: ignore[import-untyped]
import streamlit as st

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


@dataclass(frozen=True)
class StyleMarkerSpec:
    """Visual marker configuration for a playing style trace."""

    label: str
    color: str
    symbol: str
    size: float
    opacity: float
    line_width: float
    line_color: str


# Ordered back-to-front so Defenders render prominently on top of Attackers.
STYLE_MARKERS: tuple[StyleMarkerSpec, ...] = (
    StyleMarkerSpec(
        label="Unknown",
        color="#94A3B8",
        symbol="square",
        size=6.5,
        opacity=0.40,
        line_width=0.5,
        line_color="rgba(148, 163, 184, 0.6)",
    ),
    StyleMarkerSpec(
        label="Attacker",
        color="#38BDF8",
        symbol="circle",
        size=7.0,
        opacity=0.55,
        line_width=0.6,
        line_color="rgba(255, 255, 255, 0.35)",
    ),
    StyleMarkerSpec(
        label="Defender",
        color="#F59E0B",
        symbol="diamond",
        size=10.5,
        opacity=0.95,
        line_width=1.2,
        line_color="#FFFFFF",
    ),
)

BINOMIAL_CURVE_COLOR = "#10B981"
LOGISTIC_CURVE_COLOR = "#C084FC"
HIGHLIGHT_MARKER_COLOR = "#F43F5E"

MOBILE_PLOTLY_CONFIG: dict[str, Any] = {
    "responsive": True,
    "scrollZoom": False,
    "displayModeBar": "hover",
    "displaylogo": False,
    "modeBarButtonsToRemove": [
        "select2d",
        "lasso2d",
        "autoScale2d",
        "pan2d",
    ],
}


def style_figure(
    fig: go.Figure,
    *,
    height: int = 360,
    legend_bottom: bool = True,
) -> go.Figure:
    """Apply clean layout settings that adapt to Light/Dark mode and mobile widths."""
    legend_cfg: dict[str, Any]
    if legend_bottom:
        legend_cfg = {
            "bgcolor": "rgba(0,0,0,0)",
            "orientation": "h",
            "yanchor": "top",
            "y": -0.22,
            "xanchor": "center",
            "x": 0.5,
            "font": {"size": 12},
        }
        bottom_margin = 90
    else:
        legend_cfg = {
            "bgcolor": "rgba(0,0,0,0)",
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "right",
            "x": 1.0,
            "font": {"size": 12},
        }
        bottom_margin = 45

    fig.update_layout(
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"size": 13},
        title={"x": 0.01, "xanchor": "left", "font": {"size": 15}},
        legend=legend_cfg,
        margin={"l": 48, "r": 20, "t": 62, "b": bottom_margin},
        hovermode="closest",
    )
    fig.update_xaxes(
        gridcolor="rgba(128, 128, 128, 0.2)",
        zerolinecolor="rgba(128, 128, 128, 0.25)",
    )
    fig.update_yaxes(
        gridcolor="rgba(128, 128, 128, 0.2)",
        zerolinecolor="rgba(128, 128, 128, 0.25)",
    )
    return fig


def render_chart(fig: go.Figure, *, key: str | None = None) -> None:
    """Render a responsive, mobile-scroll-friendly Plotly figure in Streamlit."""
    st.plotly_chart(
        fig,
        width="stretch",
        config=MOBILE_PLOTLY_CONFIG,
        key=key,
    )
