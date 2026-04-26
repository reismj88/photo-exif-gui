from collections import Counter
from typing import Optional

import plotly.graph_objects as go

_LAYOUT = dict(
    paper_bgcolor="#1e1e1e",
    plot_bgcolor="#2a2a2a",
    font=dict(color="#d4d4d4", size=12),
    margin=dict(l=60, r=20, t=40, b=60),
    xaxis=dict(gridcolor="#3a3a3a", linecolor="#555"),
    yaxis=dict(gridcolor="#3a3a3a", linecolor="#555"),
)

_BAR_COLOR = "#4a9eff"


def _to_html(fig: go.Figure) -> str:
    return fig.to_html(
        full_html=True,
        include_plotlyjs="cdn",
        config={"displayModeBar": False},
    )


def _sorted_counts(values: list[str], top_n: Optional[int] = None) -> tuple[list[str], list[int]]:
    counts = Counter(v for v in values if v)
    items = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    if top_n:
        items = items[:top_n]
    labels, counts_list = zip(*items) if items else ([], [])
    return list(labels), list(counts_list)


def focal_length_chart(records) -> str:
    labels, counts = _sorted_counts([r.focal_length for r in records if r.focal_length])
    fig = go.Figure(
        go.Bar(x=labels, y=counts, marker_color=_BAR_COLOR),
        layout=go.Layout(title="Focal Length Distribution", **_LAYOUT),
    )
    fig.update_layout(xaxis_title="Focal Length", yaxis_title="Count")
    return _to_html(fig)


def aperture_chart(records) -> str:
    labels, counts = _sorted_counts([r.aperture for r in records if r.aperture])
    fig = go.Figure(
        go.Bar(x=labels, y=counts, marker_color="#f9a825"),
        layout=go.Layout(title="Aperture Distribution", **_LAYOUT),
    )
    fig.update_layout(xaxis_title="Aperture", yaxis_title="Count")
    return _to_html(fig)


def iso_chart(records) -> str:
    labels, counts = _sorted_counts([r.iso for r in records if r.iso])
    # Sort ISO numerically if possible
    try:
        paired = sorted(zip(labels, counts), key=lambda x: int(x[0]))
        if paired:
            labels, counts = zip(*paired)
            labels, counts = list(labels), list(counts)
    except (ValueError, TypeError):
        pass
    fig = go.Figure(
        go.Bar(x=labels, y=counts, marker_color="#66bb6a"),
        layout=go.Layout(title="ISO Distribution", **_LAYOUT),
    )
    fig.update_layout(xaxis_title="ISO", yaxis_title="Count")
    return _to_html(fig)


def camera_lens_chart(records) -> str:
    combos = [
        f"{r.camera} / {r.lens_model or 'Unknown Lens'}"
        for r in records
        if not r.missing_exif
    ]
    labels, counts = _sorted_counts(combos, top_n=15)
    fig = go.Figure(
        go.Bar(x=counts, y=labels, orientation="h", marker_color="#ab47bc"),
        layout=go.Layout(title="Shots by Camera + Lens", **_LAYOUT),
    )
    fig.update_layout(
        xaxis_title="Count",
        yaxis=dict(autorange="reversed", gridcolor="#3a3a3a", linecolor="#555"),
        height=max(300, len(labels) * 40 + 100),
    )
    return _to_html(fig)
