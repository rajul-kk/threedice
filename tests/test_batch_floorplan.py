"""threedice.batch: floorplans, grid alignment (die-edge partial cells), stack rendering, Tmap parsing."""
import pytest

np = pytest.importorskip("numpy")
from threedice.batch import (Block, Floorplan, FloorplanError, Grid, Stack, StackError,  # noqa: E402
                             read_tmap)

G = Grid(1000, 1000, 100, 100)

STK = """// a comment with floorplan "nope.flp"
material SI :
 thermal conductivity 1.30e-4 ;
 volumetric heat capacity 1.628e-12 ;
bottom heat sink :
 heat transfer coefficient 2.0e-8 ;
 temperature 300 ;
dimensions :
 chip length 1000, width 1000 ;
 cell length 100, width 100 ;
die D :
 source 50 SI ;
stack :
 die TOP D floorplan "top.flp" ;
 die BOT D floorplan "bot.flp" ;
solver :
 steady ;
 initial temperature 300 ;
 numofcores 4 ;
output :
 Tmap ( TOP, "user.txt", final ) ;
"""


def test_aligned_floorplan_passes_and_misaligned_is_refused():
    ok = Floorplan([Block("a", 100, 200, 300, 100, 1.0)])
    assert ok.aligned(G) is ok
    bad = Floorplan([Block("a", 142, 200, 300, 100, 1.0)])
    with pytest.raises(FloorplanError, match="off the"):
        bad.aligned(G)
    assert bad.aligned(G, "allow") is bad


def test_snap_moves_edges_to_grid_and_keeps_power():
    fp = Floorplan([Block("a", 142, 260, 300, 100, 2.5)])
    s = fp.aligned(G, "snap")
    b = s.blocks[0]
    assert (b.x, b.y, b.length, b.width, b.power) == (100, 300, 300, 100, 2.5)
    assert not s.misaligned(G)
    with pytest.raises(FloorplanError, match="collapses"):
        Floorplan([Block("t", 120, 120, 20, 20, 1.0)]).aligned(G, "snap")


def test_overlap_and_outside_detected():
    with pytest.raises(FloorplanError, match="overlap"):
        Floorplan([Block("a", 0, 0, 200, 200, 1), Block("b", 100, 100, 200, 200, 1)]).aligned(G)
    with pytest.raises(FloorplanError, match="overlap"):
        Floorplan([Block("a", 0, 0, 150, 150, 1), Block("b", 120, 120, 50, 50, 1)]).aligned(G, "allow")
    with pytest.raises(FloorplanError, match="leaves the chip"):
        Floorplan([Block("a", 900, 0, 200, 100, 1)]).aligned(G)


def test_edge_cell_mask_marks_partly_covered_cells_only():
    assert not Floorplan([Block("a", 100, 100, 300, 300, 1)]).edge_cell_mask(G).any()
    m = Floorplan([Block("a", 150, 100, 200, 300, 1)]).edge_cell_mask(G)   # x from 150 to 350: cols 1 and 3 partial
    assert set(np.argwhere(m)[:, 1].tolist()) == {1, 3}
    assert set(np.argwhere(m)[:, 0].tolist()) == {1, 2, 3}


def test_power_grid_to_blocks():
    p = np.zeros((10, 10))
    p[2, 3] = 0.5
    p[7, 1] = 1.5
    fp = Floorplan.from_power_grid(p, G)
    assert len(fp.blocks) == 2 and fp.total_power == 2.0 and not fp.misaligned(G)
    b = {x.name: x for x in fp.blocks}["c2_3"]
    assert (b.x, b.y, b.length, b.width) == (300, 200, 100, 100)
    assert len(Floorplan.from_power_grid(np.zeros((2, 2)), G).blocks) == 1
    big = Floorplan.from_power_grid(np.ones((10, 10)), G)   # 100 blocks: fast path of the overlap check
    assert big.aligned(G) is big


def test_render_roundtrip_text():
    t = Floorplan([Block("a", 100, 200, 300, 100, 1.25)]).render()
    assert "position 100.0, 200.0" in t and "dimension 300.0, 100.0" in t and "power values 1.25" in t


def test_stack_parse_and_render():
    s = Stack(STK, files={"top.flp": "x", "bot.flp": "y"})
    assert s.grid == G and s.dies == {"TOP": "top.flp", "BOT": "bot.flp"} and s.elements == ["TOP", "BOT"]
    text, tmaps = s.render({"TOP": "fp_TOP.flp"}, ["TOP", "BOT"])
    assert 'die TOP D floorplan "fp_TOP.flp"' in text and 'die BOT D floorplan "bot.flp"' in text
    assert "user.txt" not in text and "nope.flp" not in text
    assert text.count("numofcores") == 1 and "numofcores 1 ;" in text
    assert 'Tmap ( TOP, "tmap_TOP.txt", final ) ;' in text and tmaps == {"TOP": "tmap_TOP.txt", "BOT": "tmap_BOT.txt"}
    with pytest.raises(StackError, match="not in the stack"):
        s.render({}, ["MID"])
    with pytest.raises(StackError, match="not in the stack"):
        s.render({"MID": "x.flp"}, ["TOP"])


def test_stack_from_file_reads_referenced_inputs(tmp_path):
    (tmp_path / "m.stk").write_text(STK)
    (tmp_path / "top.flp").write_text("top")
    (tmp_path / "bot.flp").write_text("bot")
    (tmp_path / "user.txt").write_text("an old output")
    s = Stack.from_file(str(tmp_path / "m.stk"))
    assert s.files == {"top.flp": b"top", "bot.flp": b"bot"}


def test_unsupported_stacks_refused():
    with pytest.raises(StackError, match="not supported"):
        Stack(STK.replace("die D :", "microchannel 4rm :\n height 100 ;\ndie D :"))
    with pytest.raises(Exception, match="whole number"):
        Stack(STK.replace("chip length 1000", "chip length 1050"))


def test_read_tmap(tmp_path):
    p = tmp_path / "t.txt"
    p.write_text("% header\n300.100  301.200  \n302.300  303.400  \n\n")
    a = read_tmap(str(p), (2, 2))
    assert a.tolist() == [[300.1, 301.2], [302.3, 303.4]]
    with pytest.raises(ValueError, match="grid is"):
        read_tmap(str(p), (2, 3))
