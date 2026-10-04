#!/usr/bin/env python3
"""
SIDE QUEST BOT
Text-based IRL side-quest generator for Raspberry Pi + Waveshare SIM7600 4G HAT.

Friends text the Pi's SIM number:
  BORED          -> random side quest
  MYSTERY        -> secret GPS spot near home base (Randonautica-style)
  DONE <proof>   -> claim XP, e.g. "DONE found a vending machine that sells eggs"
  SKIP           -> reroll your quest (costs 5 XP)
  XP             -> your stats + title
  TOP            -> leaderboard
  FEED           -> latest completed quests from the group
  NAME <name>    -> set your display name
  HELP           -> command list

Setup:
  sudo apt install python3-serial
  python3 sidequest_bot.py
"""

import json
import math
import os
import random
import re
import time
from datetime import date, timedelta

import serial

# ---------------- CONFIG ----------------
PORT = "/dev/ttyUSB2"          # SIM7600 AT port over USB. Use "/dev/serial0" if wired by UART.
BAUD = 115200
DB_FILE = os.path.expanduser("~/sidequest_db.json")
POLL_SECONDS = 5
MIN_QUEST_SECONDS = 120        # can't claim a quest faster than this (anti-cheese)
SKIP_COST = 5
MYSTERY_XP = 40
MYSTERY_RADIUS_M = 1000        # mystery spots land within this distance of home base
HOME_BASE = None               # fallback (lat, lon) if no GPS fix, e.g. (49.2827, -123.1207)

# Only these numbers can play (recommended so randoms can't run up your SMS bill).
# Leave empty to allow anyone.  Example: {"+16045551234", "+17785550000"}
ALLOWED = set()

# ---------------- QUESTS ----------------
DIRS = ["north", "south", "east", "west", "northeast", "northwest", "southeast", "southwest"]

QUESTS = {
    10: [
        "Compliment a stranger's fit. Genuinely. No irony.",
        "Find a dog and rate it out of 10. (It's always 12/10.)",
        "Take a pic of the most aesthetic leaf you can find.",
        "Buy a snack you've never tried and give it a brutally honest review.",
        "Listen to a random song start to finish. No skips. Rate it.",
        "Text someone you haven't talked to in a year. Just 'hey'.",
        "People-watch for 5 min and invent a full backstory for someone.",
        "Find something purple within 200m.",
        "Hydration quest: drink a full glass of water. Respect the basics.",
        "Take a photo that could be an album cover.",
    ],
    25: [
        "Find the weirdest vending machine or shop sign within 2km.",
        "Walk {d}m {dir} from where you're standing. Report the first weird thing you see.",
        "Go somewhere within a 15 min walk you've never been. Rate the vibes /10.",
        "Find street art or a mural and recreate the pose.",
        "Get a free sample of something. Anything.",
        "Find the oldest-looking building nearby and make up its lore.",
        "Film 10 sec narrating your walk like a nature documentary.",
        "Find 3 things that are the same color. Instant photo dump.",
        "Learn one phrase in a language you don't speak and use it today.",
        "Walk {d}m {dir}, then {d2}m {dir2}. Whatever's there, take the main character shot.",
    ],
    50: [
        "Watch the sunset somewhere with a view. Phone away for the last 5 min.",
        "Find a hidden gem spot nobody in the group knows. Report back.",
        "Walk 5000 steps without opening social media.",
        "Go to a local thing you'd normally skip: market, gig, open mic.",
        "Reach the highest point within walking distance. Main character shot required.",
        "Cook something from scratch you've never made before.",
    ],
}

LEVELS = [
    (0, "NPC"),
    (50, "Side Character"),
    (150, "Main Character"),
    (300, "Lore Keeper"),
    (600, "Final Boss"),
    (1000, "Grass Toucher Supreme"),
]


def title(xp):
    name = LEVELS[0][1]
    for need, label in LEVELS:
        if xp >= need:
            name = label
    return name


def random_quest():
    xp = random.choices([10, 25, 50], weights=[5, 4, 1])[0]
    text = random.choice(QUESTS[xp]).format(
        d=random.choice([100, 200, 300, 500]),
        d2=random.choice([100, 200, 300]),
        dir=random.choice(DIRS),
        dir2=random.choice(DIRS),
    )
    return {"text": text, "xp": xp, "t": time.time()}


# ---------------- MODEM ----------------
class Modem:
    def __init__(self, port, baud):
        self.ser = serial.Serial(port, baud, timeout=1)

    def _read_until(self, tokens, timeout):
        buf = ""
        end = time.time() + timeout
        while time.time() < end:
            buf += self.ser.read(self.ser.in_waiting or 1).decode(errors="ignore")
            if any(t in buf for t in tokens):
                break
        return buf

    def cmd(self, c, timeout=5):
        self.ser.reset_input_buffer()
        self.ser.write((c + "\r").encode())
        return self._read_until(["\r\nOK\r\n", "ERROR"], timeout)

    def setup(self):
        for c in ["AT", "ATE0", "AT+CMGF=1", 'AT+CSCS="IRA"', "AT+CNMI=2,1,0,0,0", "AT+CGPS=1,1"]:
            self.cmd(c)
        print("Modem ready. Signal:", self.cmd("AT+CSQ").strip())

    def send_sms(self, number, text):
        text = text.encode("ascii", "replace").decode()  # plain SMS = ASCII only
        for i in range(0, len(text), 160):
            part = text[i:i + 160]
            self.ser.reset_input_buffer()
            self.ser.write(f'AT+CMGS="{number}"\r'.encode())
            if ">" not in self._read_until([">"], 5):
                print("SMS prompt failed for", number)
                return
            self.ser.write(part.encode() + b"\x1a")
            self._read_until(["\r\nOK\r\n", "ERROR"], 30)
            time.sleep(1)

    def read_sms(self):
        raw = self.cmd('AT+CMGL="ALL"', timeout=10)
        lines = raw.split("\r\n")
        msgs, i = [], 0
        while i < len(lines):
            m = re.match(r'\+CMGL: (\d+),"[^"]*","([^"]*)"', lines[i])
            if m:
                body = lines[i + 1] if i + 1 < len(lines) else ""
                msgs.append((int(m.group(1)), m.group(2), body.strip()))
                i += 2
            else:
                i += 1
        return msgs

    def delete_sms(self, idx):
        self.cmd(f"AT+CMGD={idx}")

    def gps(self):
        raw = self.cmd("AT+CGPSINFO", timeout=3)
        m = re.search(r"\+CGPSINFO: ([\d.]+),([NS]),([\d.]+),([EW])", raw)
        if not m:
            return HOME_BASE

        def to_deg(v):
            v = float(v)
            deg = int(v // 100)
            return deg + (v - deg * 100) / 60

        lat = to_deg(m.group(1)) * (1 if m.group(2) == "N" else -1)
        lon = to_deg(m.group(3)) * (1 if m.group(4) == "E" else -1)
        return lat, lon


def random_point(lat, lon, radius_m):
    d = radius_m * math.sqrt(random.random())
    b = random.uniform(0, 2 * math.pi)
    dlat = d * math.cos(b) / 111320
    dlon = d * math.sin(b) / (111320 * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


# ---------------- GAME DB ----------------
def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE) as f:
            return json.load(f)
    return {"players": {}, "feed": []}


def save_db(db):
    tmp = DB_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(db, f, indent=2)
    os.replace(tmp, DB_FILE)


def get_player(db, number):
    if number not in db["players"]:
        db["players"][number] = {
            "name": "Player" + number[-4:], "xp": 0, "done": 0,
            "streak": 0, "last_day": None, "quest": None,
        }
    return db["players"][number]


# ---------------- COMMANDS ----------------
HELP = ("SIDE QUEST BOT. Text: BORED = new quest, MYSTERY = secret spot, "
        "DONE <what u found>, SKIP, XP, TOP, FEED, NAME <you>")


def handle(db, modem, number, body):
    p = get_player(db, number)
    words = body.strip().split(maxsplit=1)
    if not words:
        return None
    cmd, arg = words[0].upper(), (words[1].strip() if len(words) > 1 else "")

    if cmd in ("BORED", "QUEST", "START"):
        p["quest"] = random_quest()
        return f"SIDE QUEST ({p['quest']['xp']}xp): {p['quest']['text']} Text DONE + proof when finished."

    if cmd == "MYSTERY":
        here = modem.gps()
        if not here:
            p["quest"] = random_quest()
            return "No GPS lock rn, so here's a regular one: " + p["quest"]["text"]
        lat, lon = random_point(*here, MYSTERY_RADIUS_M)
        p["quest"] = {"text": f"Mystery spot {lat:.5f},{lon:.5f}", "xp": MYSTERY_XP, "t": time.time()}
        return (f"MYSTERY DROP ({MYSTERY_XP}xp): maps.google.com/?q={lat:.5f},{lon:.5f} "
                "Go see what's there. Public + safe spots only, skip if sketchy.")

    if cmd == "DONE":
        q = p.get("quest")
        if not q:
            return "No active quest. Text BORED to get one."
        if time.time() - q["t"] < MIN_QUEST_SECONDS:
            return "Too fast, that's kinda sus. Actually go do it lol"
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        if p["last_day"] == yesterday:
            p["streak"] += 1
        elif p["last_day"] != today:
            p["streak"] = 1
        p["last_day"] = today
        gained = q["xp"] + min(p["streak"] - 1, 5) * 2  # streak bonus up to +10
        old = title(p["xp"])
        p["xp"] += gained
        p["done"] += 1
        p["quest"] = None
        db["feed"] = ([{"name": p["name"], "proof": arg[:80] or q["text"][:60]}] + db["feed"])[:20]
        msg = f"W. +{gained}xp (streak {p['streak']} days). Total: {p['xp']}xp."
        if title(p["xp"]) != old:
            msg += f" LEVEL UP: you're now {title(p['xp'])}!"
        return msg

    if cmd == "SKIP":
        p["xp"] = max(0, p["xp"] - SKIP_COST)
        p["quest"] = random_quest()
        return f"Rerolled (-{SKIP_COST}xp). NEW QUEST ({p['quest']['xp']}xp): {p['quest']['text']}"

    if cmd == "XP":
        return (f"{p['name']}: {p['xp']}xp, {p['done']} quests, {p['streak']} day streak. "
                f"Rank: {title(p['xp'])}")

    if cmd == "TOP":
        ranked = sorted(db["players"].values(), key=lambda r: -r["xp"])[:5]
        return "LEADERBOARD\n" + "\n".join(f"{i+1}. {r['name']} {r['xp']}xp" for i, r in enumerate(ranked))

    if cmd == "FEED":
        if not db["feed"]:
            return "Feed's empty. Be the first. Text BORED."
        return "\n".join(f"{e['name']}: {e['proof']}" for e in db["feed"][:3])

    if cmd == "NAME" and arg:
        p["name"] = re.sub(r"[^A-Za-z0-9_ ]", "", arg)[:15] or p["name"]
        return f"You're now {p['name']}. Text BORED to start."

    return HELP


# ---------------- MAIN LOOP ----------------
def main():
    modem = Modem(PORT, BAUD)
    modem.setup()
    db = load_db()
    print("Side Quest Bot is live. Waiting for texts...")
    while True:
        try:
            for idx, number, body in modem.read_sms():
                modem.delete_sms(idx)
                if ALLOWED and number not in ALLOWED:
                    continue
                print(f"<- {number}: {body}")
                reply = handle(db, modem, number, body)
                save_db(db)
                if reply:
                    print(f"-> {number}: {reply}")
                    modem.send_sms(number, reply)
        except Exception as e:
            print("error:", e)
            time.sleep(5)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
