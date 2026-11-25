"""Pure helpers, no Home Assistant imports: remote-drive commands."""

import math

MAX_VELOCITY = 0.3  # m/s, the app's limit
MAX_ROTATION = 179  # degrees
RESTART_GAP_S = 2.5  # the robot refuses app_rc_start for about a second after a session ended


class RemoteDrive:
    """Commands for one remote-drive session: app_rc_start, numbered app_rc_move, app_rc_end."""

    def __init__(self) -> None:
        self.active = False
        self.seq = 0
        self.ended_at = -math.inf

    def move(self, velocity: float, rotation: float, duration_ms: int, now: float = math.inf) -> list[tuple[str, list | dict]]:
        """No commands while a new session would start too soon after the last one ended."""
        cmds: list[tuple[str, list | dict]] = []
        if not self.active and now - self.ended_at < RESTART_GAP_S:
            return cmds
        if not self.active:
            self.active = True
            self.seq = 0
            cmds.append(("app_rc_start", []))
        self.seq += 1
        velocity = max(-MAX_VELOCITY, min(MAX_VELOCITY, velocity))
        rotation = max(-MAX_ROTATION, min(MAX_ROTATION, rotation))
        cmds.append(
            (
                "app_rc_move",
                [
                    {
                        "omega": round(math.radians(rotation), 1),
                        "velocity": round(velocity, 2),
                        "seqnum": self.seq,
                        "duration": max(100, min(3000, int(duration_ms))),
                    }
                ],
            )
        )
        return cmds

    def ended(self, now: float = -math.inf) -> None:
        """Call after the robot accepted app_rc_end, or refused app_rc_start (no session to end)."""
        self.active = False
        self.ended_at = now


CUSTOM = (106, 204, 302)  # fan, water, route the robot reports while it runs its own per-room custom mode
SEQ_THEN_MOP = 1
SMART = (110, 209, 306)  # fan, water, route of the robot's own smart mode


def then_mop_on(rooms: list[int], table: list[dict]) -> bool:
    """Every room is vacuumed before it is mopped."""
    by = {e.get("segment"): e for e in table}
    return bool(rooms) and all(by.get(r, {}).get("seq_type") == SEQ_THEN_MOP for r in rooms)


def then_mop_table(rooms: list[int], table: list[dict], settings: tuple[int, int, int], on: bool) -> list[dict]:
    """The per-room table to write. On: every room follows the global settings and is vacuumed, then mopped.
    Off: rooms this order set lose their override, other rooms keep theirs."""
    if on and (tuple(settings) == CUSTOM or None in settings):
        raise ValueError("global settings unknown or custom")
    if not rooms:
        raise ValueError("the robot lists no rooms on this map")
    fan, water, route = settings
    out = []
    by = {e.get("segment"): e for e in table}
    if None in by or len(by) != len(table):
        raise ValueError("room table without or with repeated segments")
    for seg, entry in by.items():
        if seg in rooms:
            continue
        out.append(entry)  # a room of another map or one the robot does not list: untouched
    for seg in rooms:
        entry = by.get(seg)
        if on:
            out.append({**(entry or {}), "segment": seg, "fan_power": fan, "water_box_mode": water,
                        "mop_mode": route, "repeat": (entry or {}).get("repeat", 1), "seq_type": SEQ_THEN_MOP})
        elif entry and entry.get("seq_type") != SEQ_THEN_MOP:
            out.append(entry)
    return out


def then_mop_settings(rooms: list[int], table: list[dict]) -> tuple | None:
    """The fan, water and route the order wrote for its rooms (the robot runs them in custom mode)."""
    by = {e.get("segment"): e for e in table}
    for r in rooms:
        e = by.get(r)
        if e and e.get("seq_type") == SEQ_THEN_MOP:
            return (e.get("fan_power"), e.get("water_box_mode"), e.get("mop_mode"))
    return None


def then_mop_base(settings: tuple, previous: tuple | None) -> tuple:
    """Global settings for the rooms: a value still at its custom sentinel keeps what the rooms had."""
    out = tuple(p if s == c else s for s, c, p in zip(settings, CUSTOM, previous or (None,) * 3))
    if None in out or any(v == c for v, c in zip(out, CUSTOM)):
        raise ValueError("global settings unknown or custom")
    if any(v == m for v, m in zip(out, SMART)):
        raise ValueError("the robot is in smart mode")
    return out


def then_mop_stale(rooms: list[int], table: list[dict], settings: tuple[int, int, int]) -> bool:
    """On, but the robot is not in custom mode (it ignores the room table there): a setting was changed."""
    if not then_mop_on(rooms, table) or None in settings:
        return False
    return tuple(settings) != CUSTOM
