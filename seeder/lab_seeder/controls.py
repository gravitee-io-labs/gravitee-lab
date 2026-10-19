"""Controls: the protections a playground's lessons teach, brought to the state its mode says on every run.

    seed/controls.yaml      {<control>: {<target>: <name>, flow: <name>, steps: {request: [...], response: [...]}}}

A control is one protection, named once and found in the seed files that carry it. Its target is one of

    api: <name>           steps of a flow of an API of seed/apim/apis (`flow` and `steps` say which)
    mcp-proxy: <name>     steps of a flow of an MCP proxy of seed/aim/mcp-proxies
    workspace: <name>     steps of the router of an AI workspace of seed/aim/workspaces (no `flow`)
    policy: <name>        an authorization policy of seed/authz/policies (no `flow`, no `steps`)

The seed files say the protected state: their steps are enabled and their policies deployed. SEED_PROTECTIONS sets the
state each run brings the controls to, whatever someone did in the console since:

    on      as the files say (the default: a seed that sets nothing protects everything)
    off     their steps are disabled and their policy is not deployed: a lesson switches them on

Only what a control names is brought back; the rest of an API, a proxy or a workspace is left as it is. What a control
names must exist: a step, a flow or a target that matches nothing is an error, in the seed files (a typo, a step
renamed in the console and exported) as in what the platform holds when a run aligns it. A module asks `of()` for the
controls of what it seeds, aligns the steps (`align`, `align_router`) or the policy (`status`), and calls `touched()`
when it deploys; `finish()` ends the seed, and fails on a control no module found its target for.
"""

import os
import time

from .common import SeedError, load, pause, seed_file, setting, waits_for_gateway

MODE = "SEED_PROTECTIONS"
MODES = ("on", "off")
TARGETS = ("api", "mcp-proxy", "workspace", "policy")
STEPPED = ("api", "mcp-proxy")   # the targets whose steps are in a flow
PHASES = ("request", "response")
FILE = "controls.yaml"

_matched = set()    # the controls whose target a module has found
_changed = []       # when the seed last deployed something a control changed


class Missing(SeedError):
    """A flow or a step a control names that the thing it is applied to does not have."""


class Control:
    """One control of seed/controls.yaml."""

    def __init__(self, name, spec):
        where = f"{FILE}: {name}"
        if not isinstance(spec, dict):
            raise SeedError(f"{where}: expected the target, and the steps it switches")
        unknown = set(spec) - set(TARGETS) - {"flow", "steps"}
        if unknown:
            raise SeedError(f"{where}: {', '.join(sorted(unknown))} is not a key of a control "
                            f"({', '.join(TARGETS)}, flow, steps)")
        targets = [kind for kind in TARGETS if kind in spec]
        if len(targets) != 1:
            raise SeedError(f"{where}: one target is needed, one of {', '.join(TARGETS)}")
        self.id, self.kind = name, targets[0]
        self.target = spec[self.kind]
        if not isinstance(self.target, str) or not self.target:
            raise SeedError(f"{where}: {self.kind} is the name of the {self.kind}")
        if self.kind == "policy":
            if "flow" in spec or "steps" in spec:
                raise SeedError(f"{where}: a policy has no flow and no steps")
            self.flow, self.steps = None, {}
            return
        if "flow" in spec and self.kind not in STEPPED:
            raise SeedError(f"{where}: the router of a workspace is not in a flow")
        if self.kind in STEPPED and not (isinstance(spec.get("flow"), str) and spec["flow"]):
            raise SeedError(f"{where}: flow is the name of the flow that holds the steps")
        self.flow = spec.get("flow")
        self.steps = self.read_steps(where, spec.get("steps"))

    @staticmethod
    def read_steps(where, steps):
        if not isinstance(steps, dict) or not steps or set(steps) - set(PHASES):
            raise SeedError(f"{where}: steps are {' and/or '.join(PHASES)}, each a list of step names")
        for phase, names in steps.items():
            if not (isinstance(names, list) and names and all(isinstance(n, str) and n for n in names)):
                raise SeedError(f"{where}: steps.{phase} is a list of step names")
        return {phase: list(names) for phase, names in steps.items()}

    def describe(self):
        """What it is applied to, for a message."""
        inside = f"flow '{self.flow}' of " if self.flow else ""
        return f"{inside}{self.kind} '{self.target}'"


# --- The mode and the declaration ------------------------------------------------------------

def mode():
    """The state the controls are brought to: `on` or `off`. An unknown value is an error, not a guess."""
    value = setting(MODE, "on").strip().lower()
    if value not in MODES:
        raise SeedError(f"{MODE} is '{os.getenv(MODE)}': it is one of {' or '.join(MODES)}")
    return value


def protected():
    """Whether the controls are on: steps enabled, policies deployed."""
    return mode() == "on"


def declared():
    """Every control of seed/controls.yaml, in the order of the file; none when the seed has no such file."""
    path = seed_file(FILE)
    if not path:
        return []
    spec = load(path)
    if not isinstance(spec, dict):
        raise SeedError(f"{FILE}: expected the controls, by name")
    return [Control(str(name), body) for name, body in spec.items()]


def of(kind, target):
    """The controls of what a module is about to seed (a kind and a name), which it marks as found."""
    found = [control for control in declared() if (control.kind, control.target) == (kind, target)]
    _matched.update(control.id for control in found)
    return found


def reset():
    """Forgets which controls were found and when something was deployed: a new seed starts from nothing."""
    _matched.clear()
    _changed.clear()


def begin():
    """Starts a seed: the mode is checked, and every control read, before anything is changed. Returns the controls."""
    reset()
    found = declared()
    if found:
        mode()
    return found


def finish():
    """Ends a seed that changed nothing it should not have: every control found its target. Then waits until the
    gateway has taken what the controls deployed."""
    left = [control for control in declared() if control.id not in _matched]
    if left:
        raise SeedError("; ".join(f"{FILE}: {control.id} names {control.kind} '{control.target}', which no seed file has"
                                  for control in left))
    settle()


# --- Aligning what a control names -------------------------------------------------------------

def _steps(container, control, enabled):
    """Sets `enabled` on the steps of the phases a control names, in a container with a list of steps for each phase.
    Returns whether any was not so already."""
    changed = False
    for phase, names in control.steps.items():
        for name in names:
            found = [step for step in container.get(phase) or [] if step.get("name") == name]
            if not found:
                raise Missing(f"control {control.id}: no {phase} step named '{name}' in {control.describe()}")
            for step in found:
                changed = changed or step.get("enabled", True) != enabled
                step["enabled"] = enabled
    return changed


def align(flows, controls, enabled=None):
    """Sets the steps the controls name in `flows` (the flows of an API or of an MCP proxy, as the platform or a seed file
    gives them) enabled or disabled, as the mode says. The flows are changed in place; the answer is whether any step was
    not so already."""
    enabled = protected() if enabled is None else enabled
    changed = False
    for control in controls:
        flow = next((flow for flow in flows or [] if flow.get("name") == control.flow), None)
        if flow is None:
            raise Missing(f"control {control.id}: no flow named '{control.flow}' in {control.kind} '{control.target}'")
        changed = _steps(flow, control, enabled) or changed
    return changed


def align_router(router, controls, enabled=None):
    """The same for the router of an AI workspace: {"request": [steps], "response": [steps]}."""
    enabled = protected() if enabled is None else enabled
    changed = False
    for control in controls:
        changed = _steps(router, control, enabled) or changed
    return changed


def status(controls, wanted):
    """The status an authorization policy is brought to: the file's `wanted` when no control is about it, else DEPLOYED
    (the control is on) or DRAFT (it is off)."""
    if not controls:
        return wanted
    return "DEPLOYED" if protected() else "DRAFT"


def named(controls):
    """The controls' names for a message: `pii, guard`."""
    return ", ".join(control.id for control in controls)


# --- Waiting for the gateway -------------------------------------------------------------------

def touched():
    """Something a control changed was deployed just now: the gateway has it after its next synchronizations."""
    _changed[:] = [time.time()]


def settle():
    """Waits until the gateway has had two synchronizations since the last deployment of a control."""
    if _changed and waits_for_gateway():
        ready = _changed[0] + 2 * float(setting("GATEWAY_SYNC_SECONDS", "5")) + 1
        pause(max(0.0, ready - time.time()))
        _changed.clear()
