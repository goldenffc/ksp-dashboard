# ksp-dashboard
A SpaceX-style mission control dashboard for KSP, powered by kRPC.
# kRPC Mission Control Dashboard

A SpaceX-style mission control dashboard for Kerbal Space Program, powered by kRPC. Displays real-time telemetry, engine status, fuel levels, and camera feeds in a sleek broadcast-style interface.

---

## What It Is

This is a browser-based mission control dashboard that connects to KSP via the kRPC mod. It reads live telemetry from your active vessel and displays it in a style inspired by SpaceX's real-world launch broadcasts — complete with speed and altitude gauges, a mission elapsed time clock, engine indicator panel, fuel arc, and automated event banners (LIFTOFF, MAX-Q, MECO, etc.).

It supports both **stock KSP** and **RP-1/RO** installs, with automatic fuel detection for a wide range of real-world propellants.

---

## Features

- **Live telemetry** — altitude, speed, apoapsis, periapsis, TWR, throttle, mission elapsed time
- **Engine indicator panel** — top-down engine layout showing firing state in real time, updates on staging automatically
- **Fuel gauge** — stage-aware fuel tracking using engine decouple stage logic. Supports stock LiquidFuel/SolidFuel and a wide range of RealFuels propellants including RP-1, LqdOxygen, LqdHydrogen, LqdMethane, UDMH, NTO, PBAN, PSPC, and many more
- **Automated event banners** — detects and announces LIFTOFF, MAX-Q, BOOSTER SEP, MECO, STAGE SEP, SES-1, SECO, and ORBIT ACHIEVED
- **Camera feed viewer** — integrates with JRTI (Just Read The Instructions) and HullCam for live in-game camera feeds with multi-cam layout support (1/2/3/4 feeds), auto-scroll, LOS detection, and named camera slots
- **OBS integration** — optional recording control and timer via OBS WebSocket
- **Clean view mode** — hides UI controls for a broadcast-ready view, togglable independently of OBS recording
- **Warp rate indicator** — shows physics rate, highlights yellow when the game is lagging behind real time
- **Stock and RP-1 speed modes** — switchable in settings

---

## Requirements

### KSP Side
- **Kerbal Space Program** (tested on KSP 1.12.x)
- **kRPC mod** — [GitHub](https://github.com/krpc/krpc) | [SpaceDock](https://spacedock.info/mod/69/kRPC) (CKAN preferred for easier installation)
- **RP-1 / Realism Overhaul** (optional) — for RealFuels propellant tracking and realistic physics. Stock KSP works fine without it
- **JRTI + HullCam** (optional) — required for camera feed functionality only

### Python Side
- Python 3.8 or newer
- The following packages (see Installation):
  - `krpc`
  - `flask`
  - `flask-socketio`
  - `requests`
  - `obsws-python` (optional, only needed for OBS integration)

### OBS Side (optional)
- OBS Studio
- OBS WebSocket plugin enabled (built into OBS 28+)

---

## Installation

### 1. Install the kRPC mod in KSP
Download kRPC and place it in your KSP `GameData` folder. When you launch KSP, a kRPC server window will appear — start the server before launching the dashboard.

### 2. Clone this repository
```bash
git clone https://github.com/YOUR_USERNAME/krpc-mission-control.git
cd krpc-mission-control
```
Alternatively, click the green **Code** button on the repo page and select **Download ZIP** if you don't use git.

### 3. Install Python dependencies
```bash
pip install krpc flask flask-socketio requests
```
If you want OBS integration:
```bash
pip install obsws-python
```

---

## Running the Dashboard

### Basic (local KSP, no OBS)
```bash
python dashboard_server.py 
```
Then open your browser to:
```
http://localhost:5000/spacex
```

### With OBS integration
```bash
python dashboard_server.py --obs-password YOUR_OBS_WEBSOCKET_PASSWORD
```

### KSP running on a different machine
```bash
python dashboard_server.py --krpc-host 192.168.x.x
```

### All options
| Argument | Default | Description |
|---|---|---|
| `--krpc-host` | `127.0.0.1` | IP address of the machine running KSP |
| `--port` | `5000` | Port for the dashboard web server |
| `--obs-password` | *(empty)* | OBS WebSocket password |

---

## First Time Setup in the Dashboard

When you open the dashboard for the first time, the camera settings overlay will appear automatically.

<img width="182" height="57" alt="Screenshot 2026-05-15 230930" src="https://github.com/user-attachments/assets/df92ad3c-dcc7-4097-83d4-77de7f52dfd5" />

### Speed Mode
- **STOCK** — fuel gauge tracks LiquidFuel and SolidFuel
- **RP-1** — fuel gauge tracks RealFuels propellants based on active engine stage

<img width="315" height="286" alt="Screenshot 2026-05-15 230951" src="https://github.com/user-attachments/assets/70ed7a4e-abd6-411e-940f-bf3b7607cbd0" />

Set this to match your KSP install before launch.

### Camera Setup (optional)
Requires JRTI running at `localhost:8080`. Click **AUTO-DETECT** to find available cameras automatically, or enter camera IDs manually. Make sure you start streaming the cameras or else the dashboard will not detect them. Assign cameras to slots for multi-cam layouts. Camera IDs change every KSP restart so you may need to re-detect after relaunching.

<img width="298" height="152" alt="Screenshot 2026-05-15 230859" src="https://github.com/user-attachments/assets/8a9b0358-992a-4395-b6a7-7086a1144861" />

### OBS Setup (optional)
In OBS, go to **Tools → WebSocket Server Settings**, enable the WebSocket server, and set a password. Pass that password to the dashboard with `--obs-password`. The REC button in the top bar will then control OBS recording directly and display the recording timer.

---

## Clean View Mode

Clicking the **VIEW** button in the top-right strip hides UI controls (throttle bar, warp badge, TWR, settings icon) for a clean broadcast look. Click again to restore. OBS recording automatically enables clean view as well.

---

## Known Limitations

- **Clustered rockets (e.g. Falcon Heavy)** — the engine indicator shows engines in a top-down projection. With 27 engines across 3 cores, the display will appear as 3 grouped blobs rather than individual engine dots. This is a fundamental limitation of the 2D projection. Individual engine-out detection still works — a cluster will dim when an engine fails. After booster separation the center core displays correctly as a 9-engine ring.

- **Engine position blip at staging** — at the moment of staging, engine positions may briefly shift before snapping back to the correct layout on the next update. This is a cosmetic artifact caused by the vessel's center of mass shifting mid-separation and self-corrects immediately.

- **Camera IDs reset on KSP restart** — JRTI assigns new camera IDs each session. Use AUTO-DETECT after every KSP launch.

- **kRPC server must be started before the dashboard** — if KSP is not running or the kRPC server is not open, the dashboard will show as disconnected and retry every 3 seconds automatically.
  
-  **SOI changes (e.g. Mun, Minmus)** — transitioning between spheres of influence is untested. The dashboard attempts to handle SOI changes by rebuilding flight streams automatically, but behaviour is not guaranteed.
-  **Reverting to launch** — reverting to launch will freeze all telemetry. Restarting the dashboard server resolves this.
  
- **MET clock** — there is a brief delay at the very start of a new connection while the fuel name cache is built for the first time. This is normal and only happens once per connection.

---

## Supported RealFuels Propellants

The fuel gauge automatically detects which of the following are present on the active stage:

`LiquidOxygen` · `CooledLOX` · `CooledLqdOxygen` · `RP-1` · `Kerosene` · `UDMH` · `NTO` · `N2O4` · `PSPC` · `PBAN` · `LqdHydrogen` · `Aerozine50` · `MMH` · `IRFNA` · `HTP` · `Ethanol75` · `Ethanol90` · `LqdMethane` · `CooledLCH4` · `CooledLqdMethane` · `LqdNH3` · `MON1` · `MON3`

---

<img width="1261" height="576" alt="GITHUBB" src="https://github.com/user-attachments/assets/31cad95b-562f-4e03-a585-12d2b29927b4" />

## License

MIT License — see `LICENSE` for details.
