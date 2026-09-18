import numpy as np
import plotly.graph_objects as go


def point_figure(points: dict, mode: str, point_size: float, top_view: bool = False) -> go.Figure:
    x, y, z = (points[k] for k in ("X", "Y", "Z"))
    # Centre only the display to retain precision with large survey coordinates.
    origin = np.array([np.min(x), np.min(y), np.min(z)])
    marker = {"size": point_size, "opacity": .9}
    if mode == "RGB":
        rgb = np.column_stack([points[k] for k in ("Red", "Green", "Blue")])
        rgb = np.clip(rgb / (257 if rgb.max() > 255 else 1), 0, 255).astype(int)
        marker["color"] = [f"rgb({r},{g},{b})" for r, g, b in rgb]
    else:
        key = {"Quota Z": "Z", "Intensità": "Intensity", "Classificazione": "Classification"}.get(mode, mode)
        marker.update(color=points[key], colorscale="Tealgrn" if mode == "Intensità" else "Turbo",
                      colorbar={"title": mode, "thickness": 12, "len": .6})
    figure = go.Figure(go.Scatter3d(
        x=x-origin[0], y=y-origin[1], z=z-origin[2], mode="markers", marker=marker,
        customdata=np.column_stack([x, y, z]),
        hovertemplate="X %{customdata[0]:.3f}<br>Y %{customdata[1]:.3f}<br>Z %{customdata[2]:.3f}<extra></extra>"))
    axis = {"backgroundcolor": "#0e1622", "gridcolor": "#26354a", "zerolinecolor": "#40526b", "showbackground": True}
    camera = {"eye": {"x": 0, "y": 0, "z": 2.3}, "up": {"x": 0, "y": 1, "z": 0}} if top_view else {"eye": {"x": 1.5, "y": -1.7, "z": 1.1}}
    figure.update_layout(height=560, margin={"l": 0, "r": 0, "b": 0, "t": 0},
                         paper_bgcolor="#0e1622", font={"color": "#b8c8d9"},
                         scene={"aspectmode": "data", "camera": camera,
                                "xaxis": {**axis, "title": "ΔX"}, "yaxis": {**axis, "title": "ΔY"},
                                "zaxis": {**axis, "title": "ΔZ"}}, uirevision=str(top_view))
    return figure
