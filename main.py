#!/usr/bin/python3
import asyncio
import json
import time
import threading
from typing import Set
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import paho.mqtt.client as mqtt

from database import (
    init_db, get_settings, save_settings,
    get_channels, save_channel, delete_channel, update_ghold,
    get_rules, save_rule, delete_rule
)
from engine import StateEngine

# --- WebSocket connection manager ---
class ConnectionManager:
    def __init__(self):
        self.active: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket):
        self.active.discard(ws)

    async def broadcast(self, data: dict):
        dead = set()
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.add(ws)
        self.active -= dead

manager = ConnectionManager()

# --- MQTT client ---
mqtt_client = mqtt.Client()
mqtt_connected = False
engine: StateEngine = None
loop: asyncio.AbstractEventLoop = None

# Pending GHOLD queries: topic -> channel_id
ghold_pending = {}

def mqtt_publish(topic: str, payload: str):
    if mqtt_connected:
        mqtt_client.publish(topic, payload)
        print(f"[MQTT] Published {topic} = {payload}")

async def broadcast_async(data: dict):
    if loop:
        asyncio.run_coroutine_threadsafe(manager.broadcast(data), loop)

def on_mqtt_connect(client, userdata, flags, rc):
    global mqtt_connected
    mqtt_connected = (rc == 0)
    print(f"[MQTT] {'Connected' if mqtt_connected else 'Failed'} rc={rc}")
    if mqtt_connected:
        # Subscribe to all clearone gate topics
        client.subscribe("clearone/+/GATE/+")
        # Subscribe to GHOLD responses
        client.subscribe("clearone/+/GHOLD/+/state")
        # Subscribe to control topics
        settings = get_settings()
        control_root = settings.get("control_topic", "vision-director")
        client.subscribe(f"{control_root}/engine/set")
        client.subscribe(f"{control_root}/gates/refresh")
        print(f"[MQTT] Subscribed to control topics under '{control_root}'")
        asyncio.run_coroutine_threadsafe(
            manager.broadcast({"type": "mqtt_status", "connected": True}), loop
        )

def on_mqtt_disconnect(client, userdata, rc):
    global mqtt_connected
    mqtt_connected = False
    print(f"[MQTT] Disconnected rc={rc}")
    asyncio.run_coroutine_threadsafe(
        manager.broadcast({"type": "mqtt_status", "connected": False}), loop
    )

def mqtt_refresh_gates():
    """Publish clearone/refresh to trigger gate status refresh in bridge"""
    mqtt_publish("clearone/refresh", "1")

def on_mqtt_message(client, userdata, msg):
    topic = msg.topic
    payload = msg.payload.decode("utf-8").strip()

    # Handle control topics
    settings = get_settings()
    control_root = settings.get("control_topic", "vision-director")

    if topic == f"{control_root}/engine/set":
        if engine:
            if payload.lower() in ("1", "true", "on", "start"):
                engine.running = True
            elif payload.lower() in ("0", "false", "off", "stop"):
                engine.running = False
            elif payload.lower() == "toggle":
                engine.running = not engine.running
            asyncio.run_coroutine_threadsafe(
                manager.broadcast({"type": "engine_status", "running": engine.running}), loop
            )
            print(f"[Control] Engine {'started' if engine.running else 'stopped'} via MQTT")
        return

    if topic == f"{control_root}/gates/refresh":
        mqtt_refresh_gates()
        print("[Control] Gate refresh requested via MQTT")
        return

    # Handle GHOLD responses: clearone/{dev}/GHOLD/{ch}/state
    parts = topic.split("/")
    if len(parts) == 5 and parts[2] == "GHOLD" and parts[4] == "state":
        try:
            ghold_val = float(payload)
            pending_key = f"{parts[1]}/{parts[3]}"
            if pending_key in ghold_pending:
                ch_id = ghold_pending.pop(pending_key)
                if ch_id != -1:
                    # Real channel — persist to DB and update engine
                    update_ghold(ch_id, ghold_val)
                    channels = get_channels()
                    if engine:
                        engine.channels = channels
                # Always broadcast so modal can update too
                asyncio.run_coroutine_threadsafe(
                    manager.broadcast({
                        "type": "ghold_result",
                        "channel_id": ch_id,
                        "ghold_time": ghold_val,
                        "pending_key": pending_key
                    }), loop
                )
        except ValueError:
            pass
        return

    # Handle GATE state: clearone/{dev}/GATE/{mic}
    if len(parts) == 4 and parts[2] == "GATE":
        dev = parts[1]
        mic = parts[3]
        try:
            value = int(payload)
        except ValueError:
            return

        # Find matching channel by device_id and channel number
        if engine:
            for ch in engine.channels:
                if str(ch["device_id"]) == str(dev) and str(ch["channel"]) == str(mic):
                    engine.update_state(ch["friendly_name"], value)
                    break

def connect_mqtt():
    global mqtt_client
    settings = get_settings()
    host = settings.get("mqtt_host", "localhost")
    port = int(settings.get("mqtt_port", 1883))
    user = settings.get("mqtt_user", "")
    pwd = settings.get("mqtt_pass", "")

    mqtt_client.on_connect = on_mqtt_connect
    mqtt_client.on_disconnect = on_mqtt_disconnect
    mqtt_client.on_message = on_mqtt_message

    if user:
        mqtt_client.username_pw_set(user, pwd)

    try:
        mqtt_client.connect(host, port, 60)
        mqtt_client.loop_start()
        print(f"[MQTT] Connecting to {host}:{port}")
    except Exception as e:
        print(f"[MQTT] Connection error: {e}")

def reconnect_mqtt():
    global mqtt_client, mqtt_connected
    try:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
    except:
        pass
    mqtt_connected = False
    mqtt_client = mqtt.Client()
    connect_mqtt()

def fetch_all_gholds():
    """Query GHOLD for all configured channels on startup"""
    channels = get_channels()
    for ch in channels:
        topic = f"clearone/{ch['device_id']}/GHOLD/{ch['channel']}/set"
        pending_key = f"{ch['device_id']}/{ch['channel']}"
        ghold_pending[pending_key] = ch["id"]
        mqtt_publish(topic, " ")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global loop, engine
    loop = asyncio.get_event_loop()
    init_db()

    async def broadcast_wrapper(data: dict):
        await manager.broadcast(data)

    engine = StateEngine(
        publish_callback=mqtt_publish,
        broadcast_callback=broadcast_wrapper,
        loop=loop
    )

    # Load initial config
    settings = get_settings()
    channels = get_channels()
    rules = get_rules()
    engine.update_config(rules, channels, settings.get("camera_topic", "camera/switch"))

    # Connect MQTT
    connect_mqtt()

    # Wait a moment then fetch GHOLDs and gate states
    await asyncio.sleep(2)
    fetch_all_gholds()
    mqtt_refresh_gates()

    yield

    mqtt_client.loop_stop()
    mqtt_client.disconnect()

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")

# --- WebSocket endpoint ---
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    # Send initial state on connect
    settings = get_settings()
    channels = get_channels()
    rules = get_rules()
    await ws.send_json({"type": "init", "settings": settings, "channels": channels, "rules": rules})
    await ws.send_json({"type": "mqtt_status", "connected": mqtt_connected})
    if engine:
        await ws.send_json({"type": "engine_status", "running": engine.running})
        await ws.send_json({"type": "all_states", "states": engine.states})
        await ws.send_json({"type": "switch_log", "log": engine.switch_log})

    try:
        while True:
            data = await ws.receive_json()
            await handle_ws_message(ws, data)
    except WebSocketDisconnect:
        manager.disconnect(ws)

async def handle_ws_message(ws: WebSocket, data: dict):
    msg_type = data.get("type")

    if msg_type == "save_settings":
        save_settings(data["settings"])
        reconnect_mqtt()
        await asyncio.sleep(1)
        settings = get_settings()
        engine.camera_topic = settings.get("camera_topic", "camera/switch")
        await manager.broadcast({"type": "settings_saved", "settings": settings})
        await manager.broadcast({"type": "mqtt_status", "connected": mqtt_connected})

    elif msg_type == "save_channel":
        save_channel(data["channel"])
        channels = get_channels()
        engine.channels = channels
        await manager.broadcast({"type": "channels_updated", "channels": channels})
        # Auto-fetch GHOLD for this channel
        ch = data["channel"]
        topic = f"clearone/{ch['device_id']}/GHOLD/{ch['channel']}/set"
        pending_key = f"{ch['device_id']}/{ch['channel']}"
        # Find the channel id
        for c in channels:
            if c["device_id"] == ch["device_id"] and str(c["channel"]) == str(ch["channel"]):
                ghold_pending[pending_key] = c["id"]
                break
        mqtt_publish(topic, " ")
        # Also refresh gate status
        mqtt_refresh_gates()

    elif msg_type == "delete_channel":
        delete_channel(data["id"])
        channels = get_channels()
        engine.channels = channels
        await manager.broadcast({"type": "channels_updated", "channels": channels})

    elif msg_type == "fetch_ghold":
        ch_id = data["id"]
        channels = get_channels()
        for ch in channels:
            if ch["id"] == ch_id:
                topic = f"clearone/{ch['device_id']}/GHOLD/{ch['channel']}/set"
                pending_key = f"{ch['device_id']}/{ch['channel']}"
                ghold_pending[pending_key] = ch_id
                mqtt_publish(topic, " ")
                break

    elif msg_type == "fetch_ghold_temp":
        # Modal is querying GHOLD for a channel not yet saved — use id=-1 sentinel
        dev = data.get("device_id", "").strip()
        ch = data.get("channel", "").strip()
        if dev and ch:
            topic = f"clearone/{dev}/GHOLD/{ch}/set"
            pending_key = f"{dev}/{ch}"
            ghold_pending[pending_key] = -1  # sentinel — not a real channel id
            mqtt_publish(topic, " ")

    elif msg_type == "save_rule":
        save_rule(data["rule"])
        rules = get_rules()
        engine.update_config(rules, engine.channels, engine.camera_topic)
        await manager.broadcast({"type": "rules_updated", "rules": rules})

    elif msg_type == "delete_rule":
        delete_rule(data["id"])
        rules = get_rules()
        engine.update_config(rules, engine.channels, engine.camera_topic)
        await manager.broadcast({"type": "rules_updated", "rules": rules})

    elif msg_type == "engine_toggle":
        engine.running = not engine.running
        await manager.broadcast({"type": "engine_status", "running": engine.running})

    elif msg_type == "refresh_gates":
        mqtt_refresh_gates()

    elif msg_type == "get_log":
        await ws.send_json({"type": "switch_log", "log": engine.switch_log})
