import re
import asyncio
import time
from typing import Dict, Callable, Any

class StateEngine:
    def __init__(self, publish_callback: Callable, broadcast_callback: Callable, loop: asyncio.AbstractEventLoop):
        self.states: Dict[str, int] = {}
        self.pending_timers: Dict[str, asyncio.Task] = {}
        self.publish_callback = publish_callback
        self.broadcast_callback = broadcast_callback
        self.loop = loop
        self.rules = []
        self.channels = []
        self.camera_topic = "camera/switch"
        self.switch_log = []
        self.running = True

    def _broadcast(self, data: dict):
        asyncio.run_coroutine_threadsafe(self.broadcast_callback(data), self.loop)

    def update_config(self, rules: list, channels: list, camera_topic: str):
        self.rules = [r for r in rules if r.get("enabled")]
        self.channels = channels
        self.camera_topic = camera_topic

    def get_channel_delay(self, friendly_name: str) -> float:
        for ch in self.channels:
            if ch["friendly_name"] == friendly_name:
                ghold = ch.get("ghold_time") or 0.0
                additional = ch.get("additional_hold") or 0.5
                return ghold + additional
        return 0.5

    def update_state(self, friendly_name: str, value: int):
        old_value = self.states.get(friendly_name)
        self.states[friendly_name] = value
        if old_value != value:
            rising = (old_value == 0 or old_value is None) and value == 1
            self._broadcast({
                "type": "gate_state",
                "name": friendly_name,
                "value": value
            })
            if self.running:
                asyncio.run_coroutine_threadsafe(
                    self.evaluate_rules(friendly_name, rising=rising), self.loop
                )

    def evaluate_expression(self, expression: str) -> bool:
        if not expression or not expression.strip():
            return True
        try:
            local_vars = {k: bool(v) for k, v in self.states.items()}
            identifiers = re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\b', expression)
            for ident in identifiers:
                if ident not in local_vars and ident not in ('and', 'or', 'not', 'True', 'False'):
                    local_vars[ident] = False
            expr = expression.strip()
            expr = re.sub(r'\|\|', ' or ', expr)
            expr = re.sub(r'&&', ' and ', expr)
            expr = re.sub(r'!(?!=)', ' not ', expr)
            expr = re.sub(r'==', '==', expr)
            expr = re.sub(r'!=', '!=', expr)
            return bool(eval(expr, {"__builtins__": {}}, local_vars))
        except Exception as e:
            print(f"[Engine] Expression error '{expression}': {e}")
            return False

    def is_trigger_active_simple(self, rule: dict, for_falling: bool = False) -> bool:
        """For rising: check if any trigger channel is on.
           For falling: check if any trigger channel is off (just dropped)."""
        trigger_channels = rule.get("trigger_channels") or []
        if not trigger_channels:
            return False
        if for_falling:
            # Trigger is active on falling if at least one channel is now off
            return any(self.states.get(ch, 0) == 0 for ch in trigger_channels)
        return any(self.states.get(ch, 0) == 1 for ch in trigger_channels)

    def is_blocked_simple(self, rule: dict) -> bool:
        """Check if any blocked_by channel is on"""
        blocked_by = rule.get("blocked_by") or []
        return any(self.states.get(ch, 0) == 1 for ch in blocked_by)

    async def evaluate_rules(self, changed_name: str, rising: bool = True):
        for rule in sorted(self.rules, key=lambda r: -r.get("priority", 0)):
            rule_name = rule["name"]
            rule_mode = rule.get("rule_mode", "simple")
            trigger_edge = rule.get("trigger_edge", rule.get("trigger_on", "rising"))

            # Determine if this edge matches the rule's trigger edge
            edge_matches = (trigger_edge == "rising" and rising) or \
                           (trigger_edge == "falling" and not rising)

            if not edge_matches:
                # Wrong edge — cancel pending timer if trigger no longer active
                if rule_mode == "simple":
                    trigger_active = self.is_trigger_active_simple(rule)
                else:
                    trigger_active = self.evaluate_expression(rule.get("trigger_expression", ""))
                if not trigger_active and rule_name in self.pending_timers:
                    self.pending_timers[rule_name].cancel()
                    del self.pending_timers[rule_name]
                    self._broadcast({
                        "type": "pending_trigger",
                        "rule": rule_name,
                        "camera_input": rule["camera_input"],
                        "delay": 0,
                        "state": "cancelled"
                    })
                continue

            # Check if the changed channel is relevant to this rule
            if rule_mode == "simple":
                trigger_channels = rule.get("trigger_channels") or []
                if changed_name not in trigger_channels:
                    continue
                # For falling edge — channel just went to 0, that IS the trigger
                # For rising edge — check if any trigger channel is on
                trigger_active = True if (trigger_edge == "falling" and not rising) else self.is_trigger_active_simple(rule)
            else:
                trigger_expr = rule.get("trigger_expression", "")
                if not trigger_expr:
                    continue
                trigger_active = self.evaluate_expression(trigger_expr)

            if trigger_active:
                if rule_name in self.pending_timers:
                    self.pending_timers[rule_name].cancel()

                delay = self.get_channel_delay(changed_name)

                self._broadcast({
                    "type": "pending_trigger",
                    "rule": rule_name,
                    "camera_input": rule["camera_input"],
                    "delay": delay,
                    "state": "waiting"
                })

                task = asyncio.ensure_future(
                    self._delayed_switch(rule, delay, changed_name, rising), loop=self.loop
                )
                self.pending_timers[rule_name] = task
            else:
                if rule_name in self.pending_timers:
                    self.pending_timers[rule_name].cancel()
                    del self.pending_timers[rule_name]
                    self._broadcast({
                        "type": "pending_trigger",
                        "rule": rule_name,
                        "camera_input": rule["camera_input"],
                        "delay": 0,
                        "state": "cancelled"
                    })

    async def _delayed_switch(self, rule: dict, delay: float, triggered_by: str, was_rising: bool):
        try:
            await asyncio.sleep(delay)

            rule_mode = rule.get("rule_mode", "simple")
            trigger_edge = rule.get("trigger_edge", rule.get("trigger_on", "rising"))
            current_state = self.states.get(triggered_by, 0)

            print(f"[Engine] Recheck: rule='{rule['name']}' triggered_by='{triggered_by}' "
                  f"edge={trigger_edge} current_state={current_state} states={self.states}")

            if rule_mode == "simple":
                # For rising — triggering channel must still be ON
                # For falling — triggering channel must now be OFF
                if trigger_edge == "rising" and not current_state:
                    print(f"[Engine] Cancelled — {triggered_by} no longer on")
                    self._broadcast({"type": "pending_trigger", "rule": rule["name"],
                                     "camera_input": rule["camera_input"], "delay": 0, "state": "cancelled_recheck"})
                    return
                if trigger_edge == "falling" and current_state:
                    print(f"[Engine] Cancelled — {triggered_by} still on")
                    self._broadcast({"type": "pending_trigger", "rule": rule["name"],
                                     "camera_input": rule["camera_input"], "delay": 0, "state": "cancelled_recheck"})
                    return

                # Check blocked_by — all must be off
                if self.is_blocked_simple(rule):
                    blocked = [ch for ch in (rule.get("blocked_by") or []) if self.states.get(ch, 0)]
                    print(f"[Engine] Cancelled — blocked by: {blocked}")
                    self._broadcast({"type": "pending_trigger", "rule": rule["name"],
                                     "camera_input": rule["camera_input"], "delay": 0, "state": "cancelled_blocked"})
                    return

            else:
                # Advanced mode — evaluate condition expression after delay
                condition_expr = rule.get("condition_expression", "")
                if condition_expr:
                    if not self.evaluate_expression(condition_expr):
                        print(f"[Engine] Cancelled — condition failed")
                        self._broadcast({"type": "pending_trigger", "rule": rule["name"],
                                         "camera_input": rule["camera_input"], "delay": 0, "state": "cancelled_recheck"})
                        return
                else:
                    # No condition — check triggering channel state matches edge
                    if trigger_edge == "rising" and not current_state:
                        print(f"[Engine] Cancelled — {triggered_by} no longer on")
                        self._broadcast({"type": "pending_trigger", "rule": rule["name"],
                                         "camera_input": rule["camera_input"], "delay": 0, "state": "cancelled_recheck"})
                        return
                    if trigger_edge == "falling" and current_state:
                        print(f"[Engine] Cancelled — {triggered_by} still on")
                        self._broadcast({"type": "pending_trigger", "rule": rule["name"],
                                         "camera_input": rule["camera_input"], "delay": 0, "state": "cancelled_recheck"})
                        return

            # Fire the switch
            print(f"[Engine] Firing switch to CAM {rule['camera_input']}")
            self.publish_callback(self.camera_topic, str(rule["camera_input"]))

            event = {
                "type": "switch_fired",
                "rule": rule["name"],
                "camera_input": rule["camera_input"],
                "timestamp": time.strftime("%H:%M:%S")
            }
            self.switch_log.insert(0, event)
            self.switch_log = self.switch_log[:50]
            self._broadcast(event)

            if rule["name"] in self.pending_timers:
                del self.pending_timers[rule["name"]]

        except asyncio.CancelledError:
            pass
