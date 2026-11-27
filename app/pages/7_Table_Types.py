"""Table Types: Asian vs European Glicko-2 Comparison Page.

Compares players' separate Glicko-2 ratings and win percentages on Asian-style
versus European-style tables across senior singles matches, with filters,
optional time-travel, and column-group toggles.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

import polars as pl
import streamlit as st
from lib.charts import render_chart
from lib.data import (
    data_files_exist,
    load_events,
    load_players,
    load_snapshot_timestamp,
    load_table_brands,
    load_table_type_glicko_match_ratings,
)
from lib.glicko import CONTINENT_OPTIONS
from lib.table_types import (
    build_origin_avg_elo_chart,
    build_origin_edge_chart,
    build_origin_meta_analysis,
    build_table_type_comparison,
    build_table_type_event_options,
    summarize_brand_groups,
)
from lib.theme import ensure_standalone_shell

ensure_standalone_shell(load_snapshot_timestamp())

POOL_LABELS: dict[str, Literal["men", "women"]] = {
    "Men's Singles": "men",
    "Women's Singles": "women",
}

PAGE_CSS = """
<style>
div[data-testid="stSegmentedControl"] button {
    min-height: 48px !important;
    font-size: 1.05rem !important;
    font-weight: 700 !important;
}
</style>
"""


@st.cache_data(show_spinner=False)
def _cached_comparison(
    ratings_height: int,
    players_height: int,
    gender: Literal["men", "women"],
    as_of_date: date | None,
    min_matches_per_group: int,
) -> pl.DataFrame:
    """Compute and cache the Asian vs European comparison table."""
    _ = (ratings_height, players_height)
    return build_table_type_comparison(
        load_table_type_glicko_match_ratings(),
        load_players(),
        gender=gender,
        as_of_date=as_of_date,
        min_matches_per_group=min_matches_per_group,
    )


@st.cache_data(show_spinner=False)
def _cached_event_options(
    ratings_height: int,
    events_height: int,
    gender: Literal["men", "women"],
) -> list[tuple[str, date | None]]:
    """Build and cache chronological as-of tournament dropdown options."""
    _ = (ratings_height, events_height)
    return build_table_type_event_options(
        load_table_type_glicko_match_ratings(),
        load_events(),
        gender=gender,
    )


@st.cache_data(show_spinner=False)
def _cached_brand_groups(brands_height: int) -> dict[str, list[str]]:
    """Summarize and cache sorted table brand lists per table group."""
    _ = brands_height
    return summarize_brand_groups(load_table_brands())


def _table_column_config(
    *,
    show_full_details: bool,
    show_profile: bool,
) -> tuple[list[str], dict[str, Any]]:
    """Return ordered column list and Streamlit column formatting."""
    columns: list[str] = [
        "rank",
        "flag_url",
        "player_name",
        "stronger_on",
        "rating_diff",
        "win_pct_diff",
        "total_matches",
    ]
    config: dict[str, Any] = {
        "rank": st.column_config.NumberColumn(
            "#",
            format="%d",
            width=54,
            pinned=True,
            help="Rank by absolute Elo difference between Asian and European tables",
        ),
        "flag_url": st.column_config.ImageColumn(
            "Flag",
            width="small",
            help="Represented association flag",
        ),
        "player_name": st.column_config.TextColumn(
            "Player",
            width="medium",
            pinned=True,
        ),
        "stronger_on": st.column_config.TextColumn(
            "Stronger On",
            width="small",
            help="Table surface where this player holds the higher Glicko-2 rating",
        ),
        "rating_diff": st.column_config.NumberColumn(
            "Asian Table Edge (Elo)",
            format="%+.1f",
            width="small",
            help=(
                "Asian Elo minus European Elo: '+' = stronger on Asian tables, "
                "'\u2212' = stronger on European tables"
            ),
        ),
        "win_pct_diff": st.column_config.NumberColumn(
            "Asian Table Edge (Win %)",
            format="%+.1f pp",
            width="small",
            help=(
                "Asian Win % minus European Win % (percentage points): '+' = higher "
                "win rate on Asian tables, '\u2212' = higher on European tables"
            ),
        ),
        "total_matches": st.column_config.NumberColumn(
            "Matches",
            format="%d",
            width="small",
            help="Combined rated matches across Asian and European tables",
        ),
    }

    if show_full_details:
        columns.extend(
            [
                "asian_rating",
                "european_rating",
                "asian_win_pct",
                "european_win_pct",
                "asian_matches",
                "asian_wins",
                "asian_losses",
                "european_matches",
                "european_wins",
                "european_losses",
            ]
        )
        config.update(
            {
                "asian_rating": st.column_config.NumberColumn(
                    "Asian Elo",
                    format="%.1f",
                    width="small",
                ),
                "european_rating": st.column_config.NumberColumn(
                    "Euro Elo",
                    format="%.1f",
                    width="small",
                ),
                "asian_win_pct": st.column_config.NumberColumn(
                    "Asian Win %",
                    format="%.1f%%",
                    width="small",
                ),
                "european_win_pct": st.column_config.NumberColumn(
                    "Euro Win %",
                    format="%.1f%%",
                    width="small",
                ),
                "asian_matches": st.column_config.NumberColumn(
                    "Asian M",
                    format="%d",
                    width="small",
                ),
                "asian_wins": st.column_config.NumberColumn(
                    "Asian W",
                    format="%d",
                    width="small",
                ),
                "asian_losses": st.column_config.NumberColumn(
                    "Asian L",
                    format="%d",
                    width="small",
                ),
                "european_matches": st.column_config.NumberColumn(
                    "Euro M",
                    format="%d",
                    width="small",
                ),
                "european_wins": st.column_config.NumberColumn(
                    "Euro W",
                    format="%d",
                    width="small",
                ),
                "european_losses": st.column_config.NumberColumn(
                    "Euro L",
                    format="%d",
                    width="small",
                ),
            }
        )

    if show_profile:
        columns.extend(
            ["continent", "country_name", "org", "age", "hand", "grip", "style"]
        )
        config.update(
            {
                "continent": st.column_config.TextColumn(
                    "Continent",
                    width="small",
                    help="Continent of represented association",
                ),
                "country_name": st.column_config.TextColumn(
                    "Country",
                    width="medium",
                    help="Represented country or association",
                ),
                "org": st.column_config.TextColumn(
                    "Org",
                    width="small",
                    help="Represented association code",
                ),
                "age": st.column_config.NumberColumn(
                    "Age",
                    format="%d",
                    width="small",
                    help="Player age at snapshot export time (raw DOB stays private)",
                ),
                "hand": st.column_config.TextColumn("Hand", width="small"),
                "grip": st.column_config.TextColumn("Grip", width="small"),
                "style": st.column_config.TextColumn("Style", width="small"),
            }
        )

    return columns, config


def _render_page() -> None:
    st.markdown(PAGE_CSS, unsafe_allow_html=True)
    st.title("🏟️ Table Types: Asian vs European")
    st.caption(
        "Compare player strength on Asian vs European tables across all senior "
        "singles formats. Positive (`+`) values = stronger on Asian tables; "
        "negative (`\u2212`) values = stronger on European tables."
    )

    if not data_files_exist():
        st.warning(
            "Parquet snapshots are missing in `app/data/`. "
            "Run `make app-refresh` to generate them."
        )
        return

    table_ratings = load_table_type_glicko_match_ratings()
    players = load_players()
    events = load_events()
    table_brands = load_table_brands()

    pool_choice = st.segmented_control(
        "Category",
        options=list(POOL_LABELS.keys()),
        default="Men's Singles",
        selection_mode="single",
        label_visibility="collapsed",
        width="stretch",
        key="table_types_pool_choice",
    )
    active_label = pool_choice if pool_choice in POOL_LABELS else "Men's Singles"
    gender = POOL_LABELS[active_label]

    event_options = _cached_event_options(table_ratings.height, events.height, gender)

    ctrl1, ctrl2, ctrl3, ctrl4 = st.columns([1.0, 1.15, 1.1, 1.25], gap="medium")
    with ctrl1:
        min_matches = st.slider(
            "Minimum matches per table type",
            min_value=1,
            max_value=30,
            value=5,
            step=1,
            key="table_types_min_matches",
        )
    with ctrl2:
        search_query = st.text_input(
            "Search player",
            placeholder="e.g. Wang Chuqin, Lebrun, Sun Yingsha...",
            key="table_types_search_player",
        )
    with ctrl3:
        sel_continents = st.multiselect(
            "Player continent",
            options=list(CONTINENT_OPTIONS),
            placeholder="All continents",
            key=f"table_types_continent_{gender}",
        )
    with ctrl4:
        selected_idx = st.selectbox(
            "As-of tournament",
            options=list(range(len(event_options))),
            format_func=lambda idx: event_options[idx][0],
            index=0,
            key=f"table_types_as_of_event_{gender}",
            help="Pick any tournament to view historical ratings as of that event",
        )
        as_of_date: date | None = event_options[selected_idx][1]

    base = _cached_comparison(
        table_ratings.height,
        players.height,
        gender,
        as_of_date,
        min_matches,
    )

    with st.expander("Filter by Org / Style / Hand / Grip", expanded=False):
        f1, f2, f3, f4 = st.columns(4, gap="small")
        org_opts = sorted(o for o in base["org"].drop_nulls().unique().to_list() if o)
        style_opts = sorted(base["style"].drop_nulls().unique().to_list())
        hand_opts = sorted(base["hand"].drop_nulls().unique().to_list())
        grip_opts = sorted(base["grip"].drop_nulls().unique().to_list())

        with f1:
            sel_orgs = st.multiselect("Org", org_opts, key=f"table_types_org_{gender}")
        with f2:
            sel_styles = st.multiselect(
                "Style", style_opts, key=f"table_types_style_{gender}"
            )
        with f3:
            sel_hands = st.multiselect(
                "Hand", hand_opts, key=f"table_types_hand_{gender}"
            )
        with f4:
            sel_grips = st.multiselect(
                "Grip", grip_opts, key=f"table_types_grip_{gender}"
            )

    filtered = base
    if search_query.strip():
        needle = search_query.strip().lower()
        filtered = filtered.filter(
            pl.col("player_name").str.to_lowercase().str.contains(needle, literal=True)
        )
    if sel_continents:
        filtered = filtered.filter(pl.col("continent").is_in(sel_continents))
    if sel_orgs:
        filtered = filtered.filter(pl.col("org").is_in(sel_orgs))
    if sel_styles:
        filtered = filtered.filter(pl.col("style").is_in(sel_styles))
    if sel_hands:
        filtered = filtered.filter(pl.col("hand").is_in(sel_hands))
    if sel_grips:
        filtered = filtered.filter(pl.col("grip").is_in(sel_grips))

    scoped_matches = table_ratings.filter(pl.col("gender_category") == gender)
    if as_of_date is not None:
        scoped_matches = scoped_matches.filter(
            pl.col("rating_date").is_not_null() & (pl.col("rating_date") <= as_of_date)
        )
    asian_matches_in_pool = scoped_matches.filter(
        pl.col("table_group") == "Asian"
    ).height
    euro_matches_in_pool = scoped_matches.filter(
        pl.col("table_group") == "European"
    ).height

    asian_edge = filtered.filter(pl.col("rating_diff") > 0).sort(
        ["rating_diff", "win_pct_diff", "total_matches", "player_sk"],
        descending=[True, True, True, False],
    )
    euro_edge = filtered.filter(pl.col("rating_diff") < 0).sort(
        ["rating_diff", "win_pct_diff", "total_matches", "player_sk"],
        descending=[False, False, True, False],
    )

    k1, k2, k3, k4 = st.columns(4, gap="medium")
    with k1:
        if not asian_edge.is_empty():
            top_asian = asian_edge.row(0, named=True)
            st.metric(
                "Strongest on Asian Tables",
                f"{top_asian['player_name']}",
                f"+{float(top_asian['rating_diff']):.1f} Elo vs Euro",
            )
        else:
            st.metric("Strongest on Asian Tables", "—")
    with k2:
        if not euro_edge.is_empty():
            top_euro = euro_edge.row(0, named=True)
            st.metric(
                "Strongest on European Tables",
                f"{top_euro['player_name']}",
                f"+{abs(float(top_euro['rating_diff'])):.1f} Elo vs Asian",
            )
        else:
            st.metric("Strongest on European Tables", "—")
    with k3:
        st.metric(
            "Players Shown",
            f"{filtered.height:,}",
            f"of {base.height:,} with {min_matches}+ per type",
        )
    with k4:
        st.metric(
            "Asian / European Matches",
            f"{asian_matches_in_pool:,} / {euro_matches_in_pool:,}",
        )

    st.markdown("<div style='height: 0.5rem;'></div>", unsafe_allow_html=True)

    t1, t2 = st.columns(2, gap="medium")
    with t1:
        show_full_details = st.toggle(
            "Show full details (Asian & European Elo, Win %, Matches)",
            value=False,
            key="table_types_show_full_details",
        )
    with t2:
        show_profile = st.toggle(
            "Full player info (Continent, Org, Age, Style, Hand, Grip)",
            value=False,
            key="table_types_show_profile",
        )

    if filtered.is_empty():
        st.info("No players match the current filters.")
    else:
        cols, col_config = _table_column_config(
            show_full_details=show_full_details,
            show_profile=show_profile,
        )
        st.dataframe(
            filtered.select(cols).to_pandas(),
            hide_index=True,
            width="stretch",
            height=620,
            column_config=col_config,
        )

    st.markdown("<div style='height: 1.5rem;'></div>", unsafe_allow_html=True)
    st.divider()

    st.subheader(
        "Regional Meta-Analysis: Chinese vs Non-Chinese Asian vs Rest of World"
    )
    st.caption(
        "Compares all players in the current gender pool meeting the minimum-matches "
        "and as-of-tournament thresholds (ignoring search and continent filters)."
    )
    if base.is_empty():
        st.info("Not enough players in the current slice for regional meta-analysis.")
    else:
        meta = build_origin_meta_analysis(base)
        c1, c2 = st.columns(2, gap="large")
        with c1:
            render_chart(
                build_origin_avg_elo_chart(meta),
                key=f"table_types_avg_elo_{gender}",
            )
        with c2:
            render_chart(
                build_origin_edge_chart(meta),
                key=f"table_types_edge_{gender}",
            )

        meta_display = meta.with_columns(
            pl.col("welch_p_vs_row")
            .map_elements(
                lambda p: (
                    "—"
                    if p is None
                    else ("< 0.001" if float(p) < 0.001 else f"{float(p):.3f}")
                ),
                return_dtype=pl.String,
            )
            .alias("p_display")
        ).select(
            "origin_group",
            "players",
            "avg_asian_elo",
            "avg_euro_elo",
            "mean_rating_diff",
            "rating_diff_ci95",
            "mean_win_pct_diff",
            "rel_elo_vs_row",
            "p_display",
            "significance_label",
        )
        st.dataframe(
            meta_display.to_pandas(),
            hide_index=True,
            width="stretch",
            column_config={
                "origin_group": st.column_config.TextColumn(
                    "Region Group", width="medium"
                ),
                "players": st.column_config.NumberColumn(
                    "Players", format="%d", width="small"
                ),
                "avg_asian_elo": st.column_config.NumberColumn(
                    "Avg Asian Elo", format="%.1f", width="small"
                ),
                "avg_euro_elo": st.column_config.NumberColumn(
                    "Avg Euro Elo", format="%.1f", width="small"
                ),
                "mean_rating_diff": st.column_config.NumberColumn(
                    "Mean Elo \u0394 (Asian \u2212 Euro)",
                    format="%+.1f",
                    width="small",
                ),
                "rating_diff_ci95": st.column_config.NumberColumn(
                    "\u00b195% CI", format="\u00b1%.1f", width="small"
                ),
                "mean_win_pct_diff": st.column_config.NumberColumn(
                    "Mean Win % \u0394", format="%+.1f pp", width="small"
                ),
                "rel_elo_vs_row": st.column_config.NumberColumn(
                    "Elo Shift vs RoW", format="%+.1f", width="small"
                ),
                "p_display": st.column_config.TextColumn(
                    "p-value (vs RoW)", width="small"
                ),
                "significance_label": st.column_config.TextColumn(
                    "Significance", width="medium"
                ),
            },
        )
        st.caption(
            "Because Asian and European tables are rated in separate Glicko-2 pools, "
            "raw differences can reflect a global pool offset. Comparing each group's "
            "`(Asian \u2212 European)` shift against Rest of World via a two-tailed "
            "Welch's t-test isolates whether Chinese or Non-Chinese Asian players gain "
            "significantly relative to the rest of the tour (`p < 0.05` = significant)."
        )

    st.markdown("<div style='height: 1.0rem;'></div>", unsafe_allow_html=True)
    st.divider()

    brand_groups = _cached_brand_groups(table_brands.height)
    asian_brands = ", ".join(brand_groups["Asian"]) or "None"
    euro_brands = ", ".join(brand_groups["European"]) or "None"
    other_brands = ", ".join(brand_groups["Other"])
    other_clause = (
        f"`Other` brands ({other_brands})"
        if other_brands
        else "`Other` brands (such as Nittaku, San-Ei, Xiom)"
    )

    st.markdown("### About Table Surfaces & Brand Groupings")
    st.markdown(
        "Players often talk about table surfaces making a noticeable difference in "
        "speed, bounce, and spin — much like tennis court surfaces (clay, grass, "
        "and hard court). This view splits senior singles matches by table style "
        "and runs independent Glicko-2 ratings within each group (`+` favors Asian "
        "tables, `\u2212` favors European tables).\n\n"
        "**Key takeaway:** Across WTT senior singles matches, the data does not show "
        "major overall differences between Asian and European tables \u2014 large "
        "individual gaps mostly reflect sample-size noise and do not repeat "
        "consistently from year to year.\n\n"
        f"- **Asian tables:** {asian_brands} (**Butterfly is included here for its "
        "grippier surface**)\n"
        f"- **European tables:** {euro_brands} (**Stag is assumed to be "
        "European-style even though it is made in India**)\n"
        f"- **Excluded from comparison:** {other_clause}, plus events without a "
        "known table sponsor, are left out of this comparison."
    )


_render_page()
