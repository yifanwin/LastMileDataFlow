"""Planner context state container."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class PlannerContext:
    """State shared by V2Planner, ContactManager and the head-FOV cost.

    Checkers keep their identity across rebinds; ContactManager cost terms
    hold references to them, so contents are reloaded, never replaced.
    """
    # Scene collision checkers (required)
    target_checker: object
    handle_checker: Optional[object] = None

    # Contact configuration
    contact_links: tuple = ()
    handle_contact_links: tuple = ()
    contact_allowed: bool = False

    # FOV robot and target tracking
    fov_robot: Optional[object] = None
    fov_tcp_frame: Optional[object] = None
    fov_target: Optional[object] = None
    fov_attached_offset: Optional[object] = None
    fov_cost: Optional[object] = None
    attached: bool = False

    # Workspace bounds
    local_center: Optional[object] = None
    radius: float = 0.0
    movable_base: bool = True
