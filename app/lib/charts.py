"""Shared Plotly styling, palettes, and mobile-friendly chart rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import plotly.graph_objects as go  # type: ignore[import-untyped]
import streamlit as st

# Consistent palette across all charts: ball orange is the accent, table blue
# is the quiet second series, and theory / reference lines stay neutral grey.
ACCENT_ORANGE = "#F59E0B"
TABLE_BLUE = "#5B8DB8"
NEUTRAL_GREY = "#8E8E93"

GENDER_COLORS: dict[str, str] = {
    "Men": TABLE_BLUE,
    "Women": ACCENT_ORANGE,
    "Other / Mixed": NEUTRAL_GREY,
}

FORMAT_COLORS: dict[str, str] = {
    "Best of 5": NEUTRAL_GREY,
    "Best of 7": ACCENT_ORANGE,
    "Other": "#5A5A5F",
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
        color="#64748B",
        symbol="square",
        size=6.5,
        opacity=0.55,
        line_width=0.8,
        line_color="rgba(100, 116, 139, 0.75)",
    ),
    StyleMarkerSpec(
        label="Attacker",
        color=TABLE_BLUE,
        symbol="circle",
        size=7.0,
        opacity=0.5,
        line_width=0.6,
        line_color="rgba(255, 255, 255, 0.35)",
    ),
    StyleMarkerSpec(
        label="Defender",
        color=ACCENT_ORANGE,
        symbol="diamond",
        size=10.5,
        opacity=0.95,
        line_width=1.2,
        line_color="#FFFFFF",
    ),
)

BINOMIAL_CURVE_COLOR = NEUTRAL_GREY
LOGISTIC_CURVE_COLOR = "#7FB0D9"
HIGHLIGHT_MARKER_COLOR = "#E5484D"

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
            "y": -0.28,
            "xanchor": "center",
            "x": 0.5,
            "font": {"size": 12},
        }
        bottom_margin = 108
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
        gridcolor="rgba(128, 128, 128, 0.14)",
        zerolinecolor="rgba(128, 128, 128, 0.22)",
    )
    fig.update_yaxes(
        gridcolor="rgba(128, 128, 128, 0.14)",
        zerolinecolor="rgba(128, 128, 128, 0.22)",
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
