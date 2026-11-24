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
