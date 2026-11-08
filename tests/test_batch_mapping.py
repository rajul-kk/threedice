"""threedice.batch: grid and probe-to-cell mapping (pure Python, no solver needed)."""
import pytest

np = pytest.importorskip("numpy")
from threedice.batch import Grid, GridError, Probes, map_probes, probe_values  # noqa: E402


def test_grid_shape_centres_and_truncation_refused():
    g = Grid(1000, 750, 250, 250)
    assert g.shape == (3, 4)
    assert np.allclose(g.x_centers, [125, 375, 625, 875]) and np.allclose(g.y_centers, [125, 375, 625])
    with pytest.raises(GridError, match="whole number"):
        Grid(1100, 750, 250, 250)            # 3D-ICE would silently drop 100 um


def test_cell_of_boundaries_and_far_edge():
    g = Grid(1000, 750, 250, 250)
    row, col = g.cell_of([0, 249.9, 250, 1000, 999.999], [0, 0, 250, 750, 500])
    assert col.tolist() == [0, 0, 1, 3, 3]      # boundary -> higher cell; far edge -> last cell
    assert row.tolist() == [0, 0, 1, 2, 2]
    with pytest.raises(GridError, match="outside"):
        g.cell_of([1000.1], [0])
    with pytest.raises(GridError, match="outside"):
        g.cell_of([-1], [0])


def test_cell_mode_returns_exact_field_value():
    g = Grid(1000, 750, 250, 250)
    field = np.arange(12, dtype=float).reshape(3, 4) + 300.123
    x = np.array([10, 300, 999, 625])
    y = np.array([10, 740, 300, 375])
    idx, w, cell = map_probes(g, x, y, "cell")
    v = probe_values(field, idx, w)
    assert np.array_equal(v, field[cell[:, 0], cell[:, 1]])
    assert cell.tolist() == [[0, 0], [2, 1], [1, 3], [1, 2]]


def test_bilinear_at_centres_equals_cell_and_is_linear_inside():
    g = Grid(1000, 750, 250, 250)
    xc, yc = np.meshgrid(g.x_centers, g.y_centers)
    field = 300 + 0.01 * xc + 0.02 * yc           # a linear field is reproduced exactly between centres
    idx, w, _ = map_probes(g, xc.ravel(), yc.ravel(), "bilinear")
    assert np.allclose(probe_values(field, idx, w), field.ravel(), atol=1e-12)
    x, y = np.array([200.0, 500.0, 870.0]), np.array([130.0, 600.0, 400.0])
    idx, w, _ = map_probes(g, x, y, "bilinear")
    assert np.allclose(probe_values(field, idx, w), 300 + 0.01 * x + 0.02 * y)
    assert np.allclose(w.sum(axis=1), 1.0)


def test_bilinear_clamps_in_half_cell_band_at_chip_edge():
    g = Grid(1000, 750, 250, 250)
    field = np.arange(12, dtype=float).reshape(3, 4)
    idx, w, _ = map_probes(g, [0.0, 50.0, 1000.0], [125.0, 125.0, 125.0], "bilinear")
    v = probe_values(field, idx, w)
    assert v.tolist() == [field[0, 0], field[0, 0], field[0, 3]]


def test_single_cell_axis_and_batched_fields():
    g = Grid(250, 750, 250, 250)                 # nx == 1
    fields = np.stack([np.arange(3.0).reshape(3, 1), 10 + np.arange(3.0).reshape(3, 1)])
    idx, w, _ = map_probes(g, [100.0], [250.0], "bilinear")
    assert np.allclose(probe_values(fields, idx, w), [[0.5], [10.5]])


def test_probes_validation():
    p = Probes("TOP", [1, 2], [3, 4])
    assert p.element == ["TOP", "TOP"] and p.names == ["p0", "p1"] and p.elements == ["TOP"]
    with pytest.raises(ValueError):
        Probes("TOP", [1, 2], [3])
    with pytest.raises(ValueError):
        Probes("TOP", [1, 2], [3, 4], names=["a", "a"])
    with pytest.raises(ValueError):
        map_probes(Grid(100, 100, 50, 50), [1], [1], "spline")
