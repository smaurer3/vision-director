import re
import asyncio
import time
from typing import Dict, Callable, Optional, Any

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
        """Thread-safe broadcast — can be called from any thread"""
        asyncio.run_coroutine_threadsafe(self.broadcast_callback(data), self.loop)

    def _create_task(self, coro):
        """Thread-safe task creation"""
        asyncio.run_coroutine_threadsafe(coro, self.loop)

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
            # Build vars from known states, default any unknown to 0
            local_vars = {k: bool(v) for k, v in self.states.items()}
            # Find any identifiers in the expression not yet in states and default to False
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

    async def evaluate_rules(self, changed_name: str, rising: bool = True):
        for rule in sorted(self.rules, key=lambda r: -r.get("priority", 0)):
            trigger_expr = rule.get("trigger_expression", "")
            if not trigger_expr:
                continue

            trigger_on = rule.get("trigger_on", "rising")
            rule_name = rule["name"]

            # For rising-only rules, skip evaluation entirely on falling edge
            # but still cancel any pending timer if trigger is now false
            if trigger_on == "rising" and not rising:
                trigger_active = self.evaluate_expression(trigger_expr)
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

            trigger_active = self.evaluate_expression(trigger_expr)

            if trigger_active:
                if rule_name in self.pending_timers:
                    self.pending_timers[rule_name].cancel()

                delay = self._get_expression_delay(trigger_expr)

                self._broadcast({
                    "type": "pending_trigger",
                    "rule": rule_name,
                    "camera_input": rule["camera_input"],
                    "delay": delay,
                    "state": "waiting"
                })

                task = asyncio.ensure_future(
                    self._delayed_switch(rule, delay), loop=self.loop
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

    def _get_expression_delay(self, expression: str) -> float:
        """Find the maximum delay of any friendly name referenced in the expression"""
        delay = 0.0
        for ch in self.channels:
            name = ch["friendly_name"]
            if name in expression:
                ch_delay = self.get_channel_delay(name)
                delay = max(delay, ch_delay)
        return delay if delay > 0 else 0.5

    async def _delayed_switch(self, rule: dict, delay: float):
        try:
            await asyncio.sleep(delay)

            # Re-evaluate trigger after delay
            trigger_expr = rule.get("trigger_expression", "")
            if not self.evaluate_expression(trigger_expr):
                self._broadcast({
                    "type": "pending_trigger",
                    "rule": rule["name"],
                    "camera_input": rule["camera_input"],
                    "delay": 0,
                    "state": "cancelled_recheck"
                })
                return

            # Evaluate condition
            condition_expr = rule.get("condition_expression", "")
            if condition_expr and not self.evaluate_expression(condition_expr):
                self._broadcast({
                    "type": "pending_trigger",
                    "rule": rule["name"],
                    "camera_input": rule["camera_input"],
                    "delay": 0,
                    "state": "condition_failed"
                })
                return

            # Fire the switch
            camera_input = str(rule["camera_input"])
            self.publish_callback(self.camera_topic, camera_input)

            event = {
                "type": "switch_fired",
                "rule": rule["name"],
                "camera_input": rule["camera_input"],
                "timestamp": time.strftime("%H:%M:%S")
            }
            self.switch_log.insert(0, event)
            self.switch_log = self.switch_log[:50]

            self._broadcast(event)

            rule_name = rule["name"]
            if rule_name in self.pending_timers:
                del self.pending_timers[rule_name]

        except asyncio.CancelledError:
            pass
