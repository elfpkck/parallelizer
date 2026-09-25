"""
Drives the recorder's QGIS window with a real X cursor via xdotool.

`driver.py setup` performs the unrecorded preconditions for $SCENARIO;
`driver.py record` performs the recorded steps. The scenario name is also the GIF's
file name. Positions come from `points.json` (static map/toolbar positions) and
`ui.json` (transient UI, keyed like `menu:Toolbox` or `param:DISTANCE`), both written
by `qgis_startup.py`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

OUT_DIR = Path(os.environ["OUT_DIR"])
SCENARIO = os.environ["SCENARIO"]
POINTS: dict[str, list[int]] = json.loads((OUT_DIR / "points.json").read_text())
LEFT, RIGHT = 1, 3
OUTPUT_LAYER = "Output layer with rotated features"

Target = str | list[int]
_cursor = [640, 450]


def xdotool(*args: object) -> None:
    subprocess.run(["xdotool", *map(str, args)], check=True)  # noqa: S603, S607


def locate(key: str, timeout: float = 10) -> list[int]:
    """Screen position of a `ui.json` key; a key ending in `*` matches by prefix (e.g. `popup:Road*`)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            ui: dict[str, list[int]] = json.loads((OUT_DIR / "ui.json").read_text())
        except FileNotFoundError:
            ui = {}
        for name, pos in ui.items():
            if name == key or (key.endswith("*") and name.startswith(key[:-1])):
                return pos
        time.sleep(0.1)
    sys.exit(f"{key} never appeared on screen; visible keys: {sorted(ui)}")


def resolve(target: Target) -> list[int]:
    if not isinstance(target, str):
        return target
    return POINTS[target] if target in POINTS else locate(target)


def move(target: Target, steps: int = 22, step_delay: float = 0.025) -> None:
    """Glide the cursor with ease-in-out so viewers can follow it, in one xdotool call."""
    tx, ty = resolve(target)
    sx, sy = _cursor
    args: list[object] = []
    for i in range(1, steps + 1):
        t = i / steps
        t = t * t * (3 - 2 * t)
        args += ["mousemove", int(sx + (tx - sx) * t), int(sy + (ty - sy) * t), "sleep", step_delay]
    xdotool(*args)
    _cursor[:] = [tx, ty]


def click(target: Target, button: int = LEFT, pause: float = 1.2, repeat: int = 1) -> None:
    move(target)
    time.sleep(0.3)
    xdotool("click", "--repeat", repeat, "--delay", 120, button)
    time.sleep(pause)


def click_menu_item(key: str, pause: float = 1.2) -> None:
    """Drop straight down into the open menu first: a diagonal glide across the menu bar opens other menus."""
    _, y = locate(key)
    move([_cursor[0], y], steps=10)
    click(key, pause=pause)


def type_text(text: str, pause: float = 0.8) -> None:
    xdotool("type", "--delay", 120, text)
    time.sleep(pause)


def set_number(param: str, value: str) -> None:
    click(f"param:{param}", pause=0.4)
    xdotool("key", "ctrl+a")
    type_text(value)


def drag(start: Target, end: Target, pause: float = 1.5) -> None:
    move(start)
    time.sleep(0.3)
    xdotool("mousedown", LEFT)
    move(end, steps=40, step_delay=0.03)
    time.sleep(0.3)
    xdotool("mouseup", LEFT)
    time.sleep(pause)


def jump_click(target: str) -> None:
    xdotool("mousemove", *POINTS[target])
    xdotool("click", LEFT)
    time.sleep(0.5)


def interactive_activate() -> None:
    click("btn_tool")
    click("road1", pause=2)
    click("road2", button=RIGHT, pause=1.5)
    click("road2", pause=2)


def interactive_single_click() -> None:
    for target in ("north1", "north4", "south2", "fence1"):
        click(target, pause=1.3)
    click("fence2", pause=2)


def interactive_drag_rectangle() -> None:
    drag("north_box_a", "north_box_b")
    drag("north_box_c", "north_box_d")
    drag("south_box_a", "south_box_b", pause=2)


def interactive_pick_segment() -> None:
    # Buildings align to the clicked road segment rather than the nearest one.
    click("btn_pick_ref")
    click("road0", pause=1.5)
    click("north5", pause=1.5)
    click("south4", pause=1.5)
    # Then align a chosen edge of a building: click next to its short side.
    click("road0", button=RIGHT)
    click("btn_pick_target")
    click("road2", pause=1.2)
    x, y = POINTS["north2"]
    click([x + 13, y], pause=2)


def interactive_settings() -> None:
    click("north1", pause=1.5)
    click("btn_settings")
    click("checkbox:Rotate by longest segment", pause=0.8)
    click("button:OK", pause=1)
    click("north4", pause=1.5)
    click("south1", pause=2)


def open() -> None:  # noqa: A001
    click("menubar:Processing", pause=0.6)
    click_menu_item("menu:Toolbox", pause=1.5)
    click("toolbox:search", pause=0.3)
    type_text("parallel")
    click("toolbox:leaf:Parallelizer", pause=0.6, repeat=2)
    locate("button:Run")
    time.sleep(2)


def pick_layers() -> None:
    click("param:REFERENCE_LAYER", pause=0.6)
    click("popup:Road*", pause=0.6)
    click("param:TARGET_LAYER", pause=0.6)
    click("popup:Buildings*", pause=0.8)


def run_and_compare() -> None:
    """Run, close the algorithm widget, then flick the output layer off and on to compare."""
    click("button:Run", pause=3)
    click("button:Close", pause=1.5)
    for _ in range(2):
        click(f"layer:{OUTPUT_LAYER}", pause=1.2)


def default_usage() -> None:
    pick_layers()
    run_and_compare()


def distance() -> None:
    set_number("DISTANCE", "50")
    run_and_compare()


def angle() -> None:
    set_number("ANGLE", "25")
    run_and_compare()


def by_longest() -> None:
    click("param:LONGEST", pause=0.8)
    run_and_compare()


def open_with_layers() -> None:
    open()
    pick_layers()


def jump_clicks(*targets: str) -> Callable[[], None]:
    def steps() -> None:
        for target in targets:
            jump_click(target)

    return steps


# Unrecorded steps that put QGIS in each scenario's starting state.
SETUP: dict[str, Callable[[], None]] = {
    "interactive_activate": jump_clicks(),
    "interactive_single_click": jump_clicks("btn_tool", "road1"),
    "interactive_drag_rectangle": jump_clicks("btn_tool", "road1"),
    "interactive_pick_segment": jump_clicks("btn_tool"),
    "interactive_settings": jump_clicks("btn_tool", "road1"),
    "open": jump_clicks(),
    "default_usage": open,
    "distance": open_with_layers,
    "angle": open_with_layers,
    "by_longest": open_with_layers,
}


def setup() -> None:
    SETUP[SCENARIO]()
    xdotool("mousemove", *_cursor)
    time.sleep(1)


if __name__ == "__main__":
    if sys.argv[1] == "setup":
        setup()
    else:
        time.sleep(1)
        globals()[SCENARIO]()
