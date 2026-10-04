#!/usr/bin/env python3
"""
SIDE QUEST BOT
Text-based IRL side-quest game for Raspberry Pi + Waveshare SIM7600 4G HAT.

ONE quest per day. You pick the difficulty, the bot picks the quest.

Friends text the Pi's SIM number:
  BORED (or QUEST)  -> today's menu (or your current quest if you already picked)
  EASY / MEDIUM / HARD -> lock in today's quest   (1 / 2 / 3 also work)
  MYSTERY           -> secret GPS spot near home base (counts as the day's quest)
  DONE <proof>      -> claim XP, e.g. "DONE baked banana bread, it slapped"
  XP                -> your player card
  TOP               -> leaderboard
  FEED              -> latest completed quests from the group
  NAME <name>       -> set your display name
  HELP              -> command list

Social quests ("meet up with another player") pick a real player from the group,
give them a heads-up text, and pay them bonus XP when you finish.

The "day" rolls over at 4 AM (see DAY_ROLLOVER_HOUR) so night owls get a fair shot.
Make sure the Pi's timezone is right:  sudo timedatectl set-timezone America/Vancouver

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
from datetime import datetime, timedelta

import serial

# ---------------- CONFIG ----------------
PORT = "/dev/ttyUSB2"          # SIM7600 AT port over USB. Use "/dev/serial0" if wired by UART.
BAUD = 115200
DB_FILE = os.path.expanduser("~/sidequest_db.json")
POLL_SECONDS = 5
DAY_ROLLOVER_HOUR = 4          # new quests unlock at 4 AM local time
MYSTERY_XP = 40
MYSTERY_MIN_SECONDS = 300
MYSTERY_RADIUS_M = 1000        # mystery spots land within this distance of home base
HOME_BASE = None               # fallback (lat, lon) if no GPS fix, e.g. (49.2667, -122.9500)

# Only these numbers can play (recommended so randoms can't run up your SMS bill).
# Leave empty to allow anyone.  Example: {"+16045551234", "+17785550000"}
ALLOWED = set()

# SMS segment size. Long messages are split on line breaks, not mid-word.
SMS_LIMIT = 150

# ---------------- QUESTS ----------------
# xp = reward, min = minimum seconds before DONE is accepted (anti-cheese)
TIERS = {
    "EASY":   {"xp": 10, "min": 60},
    "MEDIUM": {"xp": 25, "min": 300},
    "HARD":   {"xp": 50, "min": 600},
}

# Accepted replies -> tier
ALIASES = {
    "EASY": "EASY", "E": "EASY", "1": "EASY",
    "MEDIUM": "MEDIUM", "MED": "MEDIUM", "M": "MEDIUM", "2": "MEDIUM",
    "HARD": "HARD", "H": "HARD", "3": "HARD",
}

# Keep each one short so it fits in a single text. {player} = another real player.
QUESTS = {
    "EASY": [
        "Take a selfie with an animal. Dog, cat, duck, pigeon - all valid.",
        "Compliment a stranger's outfit. Genuinely, no irony.",
        "Make a drink you've never tried: fancy tea, mocktail, hot choc.",
        "Build a 5-song playlist for a friend and text it with a note.",
        "Dance to a full song alone like nobody's watching. Full volume.",
        "Doodle something near you for 5 min. Pic or it didn't happen.",
        "Try a snack you've never had and give a brutal honest review.",
        "Find something purple and take your best photo of it.",
        "Text someone you haven't talked to in a year. Just 'hey'.",
        "Take a photo that could be an album cover.",
        "Learn one phrase in a new language and use it today.",
        "Do a 10 min power tidy of one messy spot. Before/after pic.",
        "Make a fort with blankets. Sit in it. Eat something.",
        "Text {player} the best photo on your phone and explain it.",
    ],
    "MEDIUM": [
        "Go to a park. Sit 20 min with your phone away. Report what you saw.",
        "Bake something from scratch. Cookies, banana bread, anything.",
        "Pack a picnic and eat it outside, even if it's cold.",
        "Thrift store run: find something fun for under $5.",
        "Hit a bookstore or library, pick a book by its cover, read chapter 1.",
        "Cook a dish from a country you've never cooked from.",
        "Write a real postcard or letter and actually mail it.",
        "Try a cafe you've never been to. Chat with the barista.",
        "Go to a playground and use the swings. No shame.",
        "Make something with your hands: craft, clay, painting, anything.",
        "Challenge a friend to a game (cards, mini golf, bowling). Report the winner.",
        "Buy a fruit or veg you can't name, then figure out how to eat it.",
        "Go on a color walk: pick a color, photograph 5 things in it.",
        "Visit a viewpoint and take the main character shot.",
        "Call {player} (yes, call) and plan something to do together.",
    ],
    "HARD": [
        "Meet up with {player} IRL. Phones away for 30 min. Selfie together.",
        "Challenge {player} to a duel: cook-off, game, or race. Text the result.",
        "Watch the sunset from the highest spot you can reach.",
        "Cook a 3-course meal for someone. Dessert required.",
        "Host a game night with 3+ people. Snacks mandatory.",
        "Day trip to a nearby town, trail, or beach you've never visited.",
        "Go to an event you'd normally skip (open mic, gig, class). Talk to 2 strangers.",
        "Try a new activity: climbing gym, kayak, skating, pottery class.",
        "Do 3 random acts of kindness for strangers. Report all 3.",
        "Learn a new skill for 1 hour straight and show off what you made.",
        "Plan a mini adventure for you + {player}. Pick a spot, go, take pics.",
        "Bonfire or beach hang with friends. Bring snacks, tell one story each.",
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


def next_level(xp):
    for need, label in LEVELS:
        if need > xp:
            return need, label
    return None


def level_floor(xp):
    floor = 0
    for need, _ in LEVELS:
        if xp >= need:
            floor = need
    return floor


def xp_bar(xp):
    nxt = next_level(xp)
    if not nxt:
        return "[##########] MAX"
    lo = level_floor(xp)
    filled = int(10 * (xp - lo) / (nxt[0] - lo))
    return "[" + "#" * filled + "-" * (10 - filled) + "]"


def game_day():
    """Today's date, but the day only flips at DAY_ROLLOVER_HOUR."""
    return (datetime.now() - timedelta(hours=DAY_ROLLOVER_HOUR)).date()


# ---------------- SMS FORMATTING ----------------
_ASCII_FIXES = {
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2026": "...", "\u00a0": " ",
}


def to_ascii(text):
    for k, v in _ASCII_FIXES.items():
        text = text.replace(k, v)
    return text.encode("ascii", "ignore").decode()


def chunk(text, limit=SMS_LIMIT):
    """Split into SMS-sized parts on line/word boundaries, tagged (1/2) if needed."""
    if len(text) <= 160:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            cut = line.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            piece, line = line[:cut], line[cut:].lstrip()
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(piece)
        candidate = f"{cur}\n{line}" if cur else line
        if len(candidate) <= limit:
            cur = candidate
        else:
            if cur:
                parts.append(cur)
            cur = line
    if cur:
        parts.append(cur)
    if len(parts) > 1:
        parts = [f"{p} ({i + 1}/{len(parts)})" for i, p in enumerate(parts)]
    return parts


MENU = (
    "SIDE QUESTS\n"
    "One quest a day. Pick your difficulty:\n"
    "\n"
    "EASY   +10xp\n"
    "MEDIUM +25xp\n"
    "HARD   +50xp\n"
    "MYSTERY +40xp (secret spot)\n"
    "\n"
    "Text your pick back."
)

HELP = (
    "COMMANDS\n"
    "BORED = daily quest\n"
    "EASY / MEDIUM / HARD\n"
    "MYSTERY = secret spot\n"
    "DONE <proof>\n"
    "XP | TOP | FEED\n"
    "NAME <you>"
)


def quest_card(q, header="TODAY'S QUEST"):
    return (
        f"{header}\n"
        f"[{q['tier']} +{q['xp']}xp]\n"
        "\n"
        f"{q['text']}\n"
        "\n"
        "Text DONE + what happened."
    )


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
        for part in chunk(to_ascii(text)):
            self.ser.reset_input_buffer()
            self.ser.write(f'AT+CMGS="{number}"\r'.encode())
            if ">" not in self._read_until([">"], 5):
                print("SMS prompt failed for", number)
                return
            self.ser.write(part.encode() + b"\x1a")
            self._read_until(["\r\nOK\r\n", "ERROR"], 30)
            time.sleep(2)  # helps multi-part texts arrive in order

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
            "picked_day": None, "recent": [],
        }
    return db["players"][number]


def make_quest(db, number, p, tier):
    """Pick a quest for this tier. Social quests grab a real partner from the group."""
    others = [n for n in db["players"] if n != number]
    usable = [q for q in QUESTS[tier] if "{player}" not in q or others]
    recent = p.get("recent", [])
    template = random.choice([q for q in usable if q not in recent] or usable)
    p["recent"] = (recent + [template])[-8:]

    partner, text = None, template
    if "{player}" in template:
        partner = random.choice(others)
        text = template.format(player=db["players"][partner]["name"])
    return {
        "tier": tier, "text": text, "xp": TIERS[tier]["xp"],
        "min": TIERS[tier]["min"], "t": time.time(),
        "day": game_day().isoformat(), "partner": partner,
    }


def lock_in(db, number, p, q):
    """Save the day's quest and build the replies (plus a heads-up to any partner)."""
    p["quest"] = q
    p["picked_day"] = q["day"]
    out = [(number, quest_card(q))]
    if q.get("partner"):
        out.append((q["partner"], f"HEADS UP\n{p['name']} got a quest to meet up with you today. "
                                  "Say yes for bonus xp!"))
    return out


# ---------------- COMMANDS ----------------
def handle(db, modem, number, body):
    """Returns a list of (phone_number, text) messages to send."""
    p = get_player(db, number)
    words = body.strip().split(maxsplit=1)
    if not words:
        return []
    cmd = re.sub(r"[^A-Za-z0-9]", "", words[0]).upper()
    arg = words[1].strip() if len(words) > 1 else ""
    today = game_day().isoformat()

    def say(text):
        return [(number, text)]

    def already_picked():
        q = p.get("quest")
        if q and q.get("day") == today:
            return say(quest_card(q, "YOU ALREADY HAVE ONE"))
        return say("QUEST DONE FOR TODAY\nNice work. Fresh menu unlocks at "
                   f"{DAY_ROLLOVER_HOUR}am.")

    if cmd in ("BORED", "QUEST", "START", "PLAY"):
        return already_picked() if p.get("picked_day") == today else say(MENU)

    if cmd in ALIASES:
        if p.get("picked_day") == today:
            return already_picked()
        return lock_in(db, number, p, make_quest(db, number, p, ALIASES[cmd]))

    if cmd == "MYSTERY":
        if p.get("picked_day") == today:
            return already_picked()
        here = modem.gps()
        if not here:
            return say("No GPS lock rn. Try again in a minute, or pick EASY / MEDIUM / HARD.")
        lat, lon = random_point(*here, MYSTERY_RADIUS_M)
        q = {
            "tier": "MYSTERY", "xp": MYSTERY_XP, "min": MYSTERY_MIN_SECONDS,
            "t": time.time(), "day": today, "partner": None,
            "text": ("Go see what's at this spot:\n"
                     f"maps.google.com/?q={lat:.5f},{lon:.5f}\n"
                     "Public + safe only. Bring a friend. Skip it if it feels sketchy."),
        }
        return lock_in(db, number, p, q)

    if cmd == "DONE":
        q = p.get("quest")
        if not q:
            if p.get("picked_day") == today:
                return say("You already finished today's quest. See you tomorrow!")
            return say("No active quest. Text BORED to pick one.")
        if q.get("day") != today:
            p["quest"] = None
            return say("That quest expired. Text BORED for today's menu.")
        if time.time() - q["t"] < q.get("min", 60):
            return say("Hold up, that was quick. Go do it for real, then text DONE.")

        yesterday = (game_day() - timedelta(days=1)).isoformat()
        if p["last_day"] == yesterday:
            p["streak"] += 1
        elif p["last_day"] != today:
            p["streak"] = 1
        p["last_day"] = today

        bonus = min(p["streak"] - 1, 5) * 2  # streak bonus up to +10
        gained = q["xp"] + bonus
        old = title(p["xp"])
        p["xp"] += gained
        p["done"] += 1
        p["quest"] = None
        db["feed"] = ([{"name": p["name"], "proof": arg[:80] or q["text"].split("\n")[0][:60]}]
                      + db["feed"])[:20]

        lines = ["QUEST COMPLETE!", f"+{gained}xp" + (f" (incl. +{bonus} streak)" if bonus else ""),
                 f"Streak: {p['streak']} day{'s' if p['streak'] != 1 else ''}",
                 f"Total: {p['xp']}xp"]
        if title(p["xp"]) != old:
            lines += ["", f"LEVEL UP! You're now {title(p['xp'])}"]
        lines += ["", "Next quest unlocks tomorrow."]
        out = say("\n".join(lines))

        partner = db["players"].get(q.get("partner") or "")
        if partner:
            share = q["xp"] // 2
            partner["xp"] += share
            out.append((q["partner"], f"BONUS XP\n{p['name']} finished the meetup quest with you. "
                                      f"+{share}xp! Total: {partner['xp']}xp."))
        return out

    if cmd == "XP":
        nxt = next_level(p["xp"])
        lines = [p["name"], f"Rank: {title(p['xp'])}", f"XP: {p['xp']}", xp_bar(p["xp"])]
        if nxt:
            lines.append(f"Next: {nxt[1]} in {nxt[0] - p['xp']}xp")
        lines.append(f"Quests: {p['done']} | Streak: {p['streak']}d")
        return say("\n".join(lines))

    if cmd == "TOP":
        ranked = sorted(db["players"].items(), key=lambda kv: -kv[1]["xp"])[:5]
        rows = [f"{i + 1}. {r['name']} - {r['xp']}xp" + ("  <- you" if n == number else "")
                for i, (n, r) in enumerate(ranked)]
        return say("LEADERBOARD\n\n" + "\n".join(rows))

    if cmd == "FEED":
        if not db["feed"]:
            return say("Feed's empty. Be the first. Text BORED.")
        return say("RECENT WINS\n\n" + "\n".join(f"{e['name']}: {e['proof']}" for e in db["feed"][:3]))

    if cmd == "NAME" and arg:
        p["name"] = re.sub(r"[^A-Za-z0-9_ ]", "", arg)[:15].strip() or p["name"]
        return say(f"Got it, you're {p['name']}.\nText BORED to start.")

    return say(HELP)


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
                replies = handle(db, modem, number, body)
                save_db(db)
                for to, text in replies:
                    print(f"-> {to}: {text}")
                    modem.send_sms(to, text)
        except Exception as e:
            print("error:", e)
            time.sleep(5)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()