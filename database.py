import sqlite3
import json
from pathlib import Path

DB_PATH = Path("visionmixer.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS dsp_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            channel INTEGER NOT NULL,
            friendly_name TEXT NOT NULL UNIQUE,
            additional_hold REAL DEFAULT 0.5,
            ghold_time REAL DEFAULT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS logic_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            camera_input INTEGER NOT NULL,
            trigger_expression TEXT NOT NULL,
            condition_expression TEXT DEFAULT '',
            enabled INTEGER DEFAULT 1,
            priority INTEGER DEFAULT 0,
            trigger_on TEXT DEFAULT 'rising'
        )
    """)

    # Add trigger_on column if upgrading from older DB
    try:
        c.execute("ALTER TABLE logic_rules ADD COLUMN trigger_on TEXT DEFAULT 'rising'")
    except:
        pass

    # Default settings
    defaults = {
        "mqtt_host": "localhost",
        "mqtt_port": "1883",
        "mqtt_user": "",
        "mqtt_pass": "",
        "camera_topic": "camera/switch",
    }
    for key, value in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))

    conn.commit()
    conn.close()

def get_settings():
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}

def save_settings(data: dict):
    conn = get_db()
    for key, value in data.items():
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

def get_channels():
    conn = get_db()
    rows = conn.execute("SELECT * FROM dsp_channels ORDER BY friendly_name").fetchall()
    conn.close()
    return [dict(row) for row in rows]

def save_channel(data: dict):
    conn = get_db()
    if data.get("id"):
        conn.execute("""
            UPDATE dsp_channels SET device_id=?, channel=?, friendly_name=?, additional_hold=?
            WHERE id=?
        """, (data["device_id"], data["channel"], data["friendly_name"], data["additional_hold"], data["id"]))
    else:
        conn.execute("""
            INSERT INTO dsp_channels (device_id, channel, friendly_name, additional_hold)
            VALUES (?, ?, ?, ?)
        """, (data["device_id"], data["channel"], data["friendly_name"], data["additional_hold"]))
    conn.commit()
    conn.close()

def delete_channel(channel_id: int):
    conn = get_db()
    conn.execute("DELETE FROM dsp_channels WHERE id=?", (channel_id,))
    conn.commit()
    conn.close()

def update_ghold(channel_id: int, ghold_time: float):
    conn = get_db()
    conn.execute("UPDATE dsp_channels SET ghold_time=? WHERE id=?", (ghold_time, channel_id))
    conn.commit()
    conn.close()

def get_rules():
    conn = get_db()
    rows = conn.execute("SELECT * FROM logic_rules ORDER BY priority DESC, name").fetchall()
    conn.close()
    return [dict(row) for row in rows]

def save_rule(data: dict):
    conn = get_db()
    if data.get("id"):
        conn.execute("""
            UPDATE logic_rules SET name=?, camera_input=?, trigger_expression=?,
            condition_expression=?, enabled=?, priority=?, trigger_on=?
            WHERE id=?
        """, (data["name"], data["camera_input"], data["trigger_expression"],
              data.get("condition_expression", ""), data.get("enabled", 1),
              data.get("priority", 0), data.get("trigger_on", "rising"), data["id"]))
    else:
        conn.execute("""
            INSERT INTO logic_rules (name, camera_input, trigger_expression, condition_expression, enabled, priority, trigger_on)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (data["name"], data["camera_input"], data["trigger_expression"],
              data.get("condition_expression", ""), data.get("enabled", 1),
              data.get("priority", 0), data.get("trigger_on", "rising")))
    conn.commit()
    conn.close()

def delete_rule(rule_id: int):
    conn = get_db()
    conn.execute("DELETE FROM logic_rules WHERE id=?", (rule_id,))
    conn.commit()
    conn.close()
