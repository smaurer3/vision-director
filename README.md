# Vision Mixer Brain

An open source, MQTT-driven automated camera switching engine for live production environments. Designed for churches, theatres, and live events using DSP gate status to intelligently drive camera selection.

Built to replace expensive proprietary auto-switching systems (think Crestron Automate VX) with a flexible, fully configurable alternative that runs on any hardware.

---

## Features

- **MQTT-only architecture** — subscribes and publishes to a single broker, integrates cleanly with Bitfocus Companion, Home Assistant, or any MQTT-aware system
- **DSP gate-driven switching** — reads ClearOne Converge gate status via the [clearone-mqtt-bridge](https://github.com/yourname/clearone-mqtt-bridge)
- **Intelligent false trigger elimination** — waits for `GHOLD + additional hold time` then re-evaluates before switching, eliminating mic clicks, pops and transients
- **Full boolean expression engine** — trigger and condition expressions support `&&`, `||`, `!`, `==`, `!=` and parentheses using friendly variable names
- **Auto GHOLD query** — fetches gate hold time from the DSP on startup and when channels are configured, no manual entry required
- **Live web UI** — dark, production-grade interface with real-time WebSocket updates, no page refreshes
- **Per-channel delay tuning** — set additional hold time per channel on top of the DSP's own GHOLD value
- **Priority rules** — multiple rules evaluated in priority order
- **Engine start/stop** — disable switching without changing config
- **Switch log** — timestamped history of every camera switch fired

---

## How It Works

```
ClearOne DSP
    │ (GREPORT gate status)
    ▼
clearone-mqtt-bridge  ──►  MQTT Broker  ◄──  Vision Mixer Brain
                                │                      │
                                │                      ▼
                           Companion           camera/switch topic
                                │
                                ▼
                           ATEM Switcher
```

1. The ClearOne MQTT bridge connects to your DSP and publishes gate state changes to `clearone/{device}/GATE/{mic}` as `1` or `0`
2. Vision Mixer Brain subscribes to these topics and maps them to friendly variable names
3. When a trigger expression evaluates true, a timer starts (`GHOLD + additional hold`)
4. After the timer, the trigger is re-evaluated — if still true, the condition expression is checked
5. If both pass, the brain publishes the camera input number to your configured camera topic
6. Companion picks this up and executes the ATEM switch

---

## Requirements

- Python 3.10+
- MQTT broker (Mosquitto recommended)
- [clearone-mqtt-bridge](https://github.com/yourname/clearone-mqtt-bridge) for DSP gate status

```bash
pip install -r requirements.txt
```

---

## Running

```bash
uvicorn main:app --host 0.0.0.0 --port 8080
```

Then open `http://localhost:8080` in your browser.

---

## Configuration

All configuration is done through the web UI — no config files to edit.

### Settings
- MQTT broker host, port, username, password
- Camera switch output topic (default: `camera/switch`)

### DSP Channels
Add each mic channel you want to monitor:
- **Device ID** — ClearOne device ID (e.g. `10` or `H2`)
- **Mic Channel** — channel number (1-8)
- **Variable Name** — friendly name used in expressions (e.g. `lectern_mic`)
- **Additional Hold** — extra delay in seconds added on top of the DSP's GHOLD value

GHOLD is automatically fetched from the DSP when you configure a channel.

### Logic Rules
Define camera switching rules using boolean expressions:

| Field | Description |
|---|---|
| Name | Friendly name for the rule |
| Camera Input | ATEM input number to switch to |
| Priority | Higher priority rules evaluated first |
| Trigger Expression | Expression that starts the switch timer |
| Condition Expression | Expression re-evaluated after delay — switch only fires if true |

**Expression examples:**
```
(audience1 || audience2) && !lectern_mic
song_leader && !roving_mic
lectern_mic == 1
roving_mic && !song_leader && !lectern_mic
```

---

## Expression Reference

| Operator | Meaning |
|---|---|
| `&&` | AND |
| `\|\|` | OR |
| `!` | NOT |
| `==` | Equals |
| `!=` | Not equals |

Variable names are the friendly names you assign to DSP channels. Each evaluates to `1` (gate open) or `0` (gate closed).

---

## Companion Integration

In Bitfocus Companion, create a trigger on MQTT topic `camera/switch`:
- When payload = `1` → switch ATEM to input 1
- When payload = `2` → switch ATEM to input 2
- etc.

Or use a generic trigger that reads the payload value and uses it directly as the ATEM input.

---

## Running as a Service (macOS launchd)

Create `/Library/LaunchDaemons/com.visionmixer.brain.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.visionmixer.brain</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/uvicorn</string>
        <string>main:app</string>
        <string>--host</string>
        <string>0.0.0.0</string>
        <string>--port</string>
        <string>8080</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/visionmixer</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
```

---

## Acknowledgements

Inspired by a conversation about automating a church AV system using open source tools to replicate $20k+ commercial functionality for almost nothing. Built with FastAPI, paho-mqtt, and plain HTML/CSS/JS.
