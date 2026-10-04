# Side Quest Bot

A text-message side-quest game running on a **Raspberry Pi 3** with a **Waveshare SIM7600 4G HAT**.
Friends text the Pi's SIM number and get IRL quests back, earn XP, level up, and compete on a leaderboard.

| Text this | What happens |
|---|---|
| `BORED` | Get a random side quest (10, 25 or 50 XP) |
| `MYSTERY` | Get a Google Maps link to a random spot within 1 km of the Pi |
| `DONE <proof>` | Claim your XP, e.g. `DONE found a vending machine that sells eggs` |
| `SKIP` | Reroll your quest (costs 5 XP) |
| `XP` | Your stats and title |
| `TOP` | Leaderboard |
| `FEED` | Latest completed quests from the group |
| `NAME <name>` | Set your display name |
| `HELP` | Command list |

---

## 1. What you need

- Raspberry Pi 3 (Model B or B+)
- Waveshare SIM7600 4G HAT, with its USB cable, 4G antenna and GPS antenna
- microSD card, 16 GB or bigger
- **Power supply: 5V / 2.5A micro-USB.** The official Pi 3 supply is best. The modem draws power spikes, and a weak supply causes random reboots.
- **SIM card with a texting plan.** An unlimited-text plan is best if lots of friends play.
- A way to write the SD card: an SD slot on your computer, or a USB SD card reader (about $10)
- One way to type commands on the Pi (pick one in step 3):
  - your phone's hotspot, or
  - an Ethernet cable, or
  - an HDMI monitor/TV and a USB keyboard

### Prepare the SIM first

Put the SIM in your phone and **turn off the SIM PIN** in your phone's settings. The modem can't use a SIM that's locked with a PIN.

---

## 2. Install the operating system on the SD card

1. On your computer, download **Raspberry Pi Imager** from <https://www.raspberrypi.com/software/>.
2. Put the microSD card in your computer (or in the USB card reader) and open Imager.
3. Choose:
   - **Device:** Raspberry Pi 3
   - **OS:** Raspberry Pi OS (other) → **Raspberry Pi OS Lite (64-bit)**
   - **Storage:** your SD card
4. When asked about customisation, click **Edit settings** and set:
   - **Hostname:** `sidequest`
   - **Username and password:** write these down!
   - **Wi-Fi:** your phone hotspot's name and password, with country set to `CA`. Skip this if you're using Ethernet or a monitor.
   - **Services tab:** turn on **SSH** with password login
   - **Raspberry Pi Connect** (optional but handy): turn it on and sign in with a free Raspberry Pi ID. It lets you control the Pi from a web browser on any network.
5. Click **Write** and wait. It can take 10+ minutes on slow Wi-Fi.

> **Don't use school Wi-Fi.** It usually needs a username/password login that Imager can't set up, it blocks devices from seeing each other, and it may break school rules. Use your phone's hotspot instead.

### What the card looks like on Windows afterwards

Windows will show one small drive called **bootfs**. The rest of the card shows as **Unknown** filesystem with **0 B** free. That's normal, because it's a Linux format Windows can't read.

**If Windows asks to format the disk, click Cancel.** Formatting wipes the system.

---

## 3. Choose how you'll control the Pi

The Pi has no screen or keyboard of its own, so you need a way to type commands on it. Pick **one** option.

### Option A: Phone hotspot (recommended)

1. Turn on your phone's hotspot.
   - **iPhone:** turn on **Maximize Compatibility**
   - **Android:** set the hotspot band to **2.4 GHz**
   - The Pi 3 can only see 2.4 GHz Wi-Fi.
2. Keep the hotspot screen open. Some phones switch it off when nothing is connected.
3. Then connect with either:
   - **Raspberry Pi Connect:** go to <https://connect.raspberrypi.com>, sign in, click your Pi, and choose **Remote shell**. Your laptop can be on any network.
   - **ssh:** connect your laptop to the **same hotspot**, open PowerShell (Windows) or Terminal (Mac), and run:
     ```
     ssh yourusername@sidequest.local
     ```

### Option B: Ethernet cable (no Wi-Fi or internet needed)

1. Plug an Ethernet cable from the Pi to your laptop. Use a USB-to-Ethernet adapter if your laptop has no Ethernet port.
2. Power on the Pi and wait 3–5 minutes, then run:
   ```
   ssh yourusername@sidequest.local
   ```
3. If it can't find the Pi, share your laptop's connection on Windows:
   - Press **Windows key + R**, type `ncpa.cpl`, and press Enter.
   - Right-click your **Wi-Fi** adapter → **Properties** → **Sharing** tab.
   - Tick **"Allow other network users to connect"** and choose your **Ethernet** adapter.
   - Unplug and replug the Pi's power, wait a few minutes, and try ssh again.

### Option C: Monitor and keyboard (fully offline)

Plug the Pi into a TV or monitor with an HDMI cable, plug in a USB keyboard, and log in with the username and password you set in Imager. You type commands straight on the Pi.

### Cables that will NOT work

- **A USB cable into the Pi's micro-USB port:** that port is power only.
- **USB from the Pi's big USB ports to your laptop:** both ends are "host" ports and can't talk to each other.

---

## 4. Copy the bot files onto the SD card

Do this before putting the card in the Pi. It works with or without internet.

1. Put the SD card in your computer and open the **bootfs** drive.
2. Drag in **`sidequest_bot.py`**.
3. **Only if the Pi won't have internet:** also download the serial add-on and drag it into bootfs:
   - Go to <https://packages.debian.org/trixie/python3-serial>
   - Under **Download**, click **all**, then any mirror link
   - You'll get a file like `python3-serial_3.5-2_all.deb`
4. Eject the card safely from your computer.

On the Pi, these files show up in `/boot/firmware/`.

---

## 5. Assemble the hardware

**Keep the Pi unplugged while you do this.**

1. Put the SD card into the Pi.
2. Press the HAT onto the Pi's 40 GPIO pins, lining up the corners.
3. Connect the **HAT's USB port** to one of the **Pi's USB ports** with the small USB cable.
4. Screw on the antennas:
   - 4G antenna → **MAIN**
   - GPS antenna → **GNSS**
5. Insert the SIM into the HAT's SIM slot.
6. Plug in the power supply.
7. If the HAT's lights don't come on after about 30 seconds, hold its **PWR** button for 1–2 seconds.

### Wait for first boot

The first boot takes **3–5 minutes** and may restart once.

- **Steady red light:** it has power.
- **Lots of green flickering:** it's busy setting up.
- **Green mostly off with occasional blinks:** it's ready.
- **No green flicker at all:** the card didn't write properly, so re-flash it.

With the hotspot option, the Pi is ready when it appears as a connected device on your phone's hotspot screen. You can't break anything by trying to connect too early. If ssh hangs for more than 30 seconds, press **Ctrl+C**, wait a minute and try again.

You're connected when you see a line ending in `$`.

---

## 6. Install the serial add-on

The bot needs **python3-serial** to talk to the modem. First check whether it's already installed:

```
python3 -c "import serial"
```

If nothing is printed, it's already there. Skip to step 7.

If you see an error, install it one of these ways.

**With internet (hotspot):**
```
sudo apt update
sudo apt install python3-serial minicom -y
```

**Without internet (using the file from step 4):**
```
sudo apt install /boot/firmware/python3-serial*.deb
```

> If this complains about versions, your Raspberry Pi OS is based on the older Debian release. Download the file from <https://packages.debian.org/bookworm/python3-serial> instead and try again.

---

## 7. Check that the modem works

```
ls /dev/ttyUSB*
```

You should see `ttyUSB0` through `ttyUSB4`. If nothing appears, check the HAT's USB cable and power.

**Optional deeper test** (needs `minicom`, installed in step 6 with internet):

```
sudo minicom -D /dev/ttyUSB2
```

Type each command and press Enter:

| Command | Good reply | Meaning |
|---|---|---|
| `AT` | `OK` | The modem is talking |
| `AT+CPIN?` | `+CPIN: READY` | The SIM is unlocked. Anything else means the SIM PIN is still on. |
| `AT+CSQ` | `+CSQ: 15,99` | Signal strength. The first number should be above 10. |

Exit minicom with **Ctrl+A**, then **X**, then **Enter**.

---

## 8. Set up the bot

Copy the bot into your home folder:

```
cp /boot/firmware/sidequest_bot.py ~
```

> If you're using ssh with internet instead, you can copy it from your computer with this command, run on your **computer** in the folder where the file is:
> ```
> scp sidequest_bot.py yourusername@sidequest.local:~
> ```

Open it to add your friends' numbers:

```
nano ~/sidequest_bot.py
```

Find the line `ALLOWED = set()` and change it to your friends' numbers, including `+1`:

```
ALLOWED = {"+16045551234", "+17785550000"}
```

This stops random people from playing and running up your SMS bill.

**Optional settings** near the top of the file:

- `PORT`: change this if your modem isn't on `/dev/ttyUSB2`
- `HOME_BASE`: set it to your coordinates, e.g. `(49.2827, -123.1207)`, so `MYSTERY` still works when the Pi is indoors without a GPS signal

Save with **Ctrl+O**, then **Enter**. Exit with **Ctrl+X**.

---

## 9. Run it

```
python3 ~/sidequest_bot.py
```

From your phone, text **`BORED`** to the Pi's SIM number. A quest should come back within about 10 seconds.

Press **Ctrl+C** to stop the bot.

---

## 10. Start the bot automatically on boot

```
crontab -e
```

Choose option **1** (nano) if asked. Add this line at the bottom, replacing `yourusername` with your username:

```
@reboot sleep 30 && python3 /home/yourusername/sidequest_bot.py >> /home/yourusername/bot.log 2>&1
```

Save and exit. Now the bot starts every time the Pi gets power.

After this, you can unplug the monitor, keyboard and cables. The bot only needs power and the SIM, because texts go over the cell network, not Wi-Fi.

To see what the bot has been doing:

```
cat ~/bot.log
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| ssh hangs or can't find the Pi | Make sure your laptop and Pi are on the **same** network. Try the Pi's IP address instead of `sidequest.local`. Or use Raspberry Pi Connect. |
| Pi never shows up on the hotspot | Check the hotspot is on 2.4 GHz / Maximize Compatibility. The Wi-Fi name or password in Imager may be mistyped, so re-flash the card. |
| `Permission denied` opening `/dev/ttyUSB2` | Run `sudo usermod -aG dialout $USER`, then reboot with `sudo reboot`. |
| No `/dev/ttyUSB*` devices | Check the USB cable between the HAT and Pi. Press the HAT's PWR button. |
| `AT+CPIN?` doesn't say `READY` | Turn off the SIM PIN using your phone, then put the SIM back in the HAT. |
| No texts coming back | Check signal with `AT+CSQ`. Check the sender's number is in `ALLOWED`, including `+1`. Check that your plan includes SMS. |
| `MYSTERY` says no GPS lock | Put the GPS antenna near a window or outside, or set `HOME_BASE`. |
| Pi randomly reboots | Use a stronger power supply (5V / 2.5A or more). |
| Windows asks to format the SD card | Click **Cancel**. That's the Linux part of the card, and it's supposed to be unreadable on Windows. |

### Notes

- Bot replies are plain text only, with no emoji. Emoji force a different SMS encoding that cuts each message from 160 to 70 characters.
- `MYSTERY` spots are random. Only go if the spot is public and safe, and skip it if it looks sketchy.
- Only add friends who have agreed to play.
