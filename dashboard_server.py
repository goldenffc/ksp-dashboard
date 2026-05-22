"""
kRPC Mission Control Dashboard — Backend Server
Connects to kRPC, polls telemetry, and pushes it to the browser via WebSocket.

Usage:
    python dashboard_server.py [--krpc-host 127.0.0.1] [--port 5000]
"""

import argparse
import threading
import time
import krpc
import requests
from flask import Flask, render_template, Response, request, stream_with_context
from flask_socketio import SocketIO

app = Flask(__name__)
app.config["SECRET_KEY"] = "krpc-dashboard"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Shared state — written by the telemetry thread, read by SocketIO emitter
telemetry_lock = threading.Lock()
telemetry = {
    "connected":        False,
    "vessel_name":      "—",
    "altitude":         0,
    "speed":            0,
    "apoapsis":         0,
    "periapsis":        0,
    "throttle":         0,
    "twr":              0,
    "dynamic_pressure": 0,
    "fuel_pct":         0,
    "fuel_pct_rp1":     0,
    "fuel_amount":      0,
    "fuel_max":         0,
    "current_stage":    0,
    "mission_time":     0,
    "engines":          [],
    "warp_rate":        1.0,
}


# RealFuels propellant names to track for the fuel gauge
_RF_PROPS_BASE = [
    "LiquidOxygen", "CooledLOX", "CooledLqdOxygen",
    "RP-1", "Kerosene",
    "UDMH", "NTO", "N2O4", "PSPC",
    "LqdHydrogen", "Aerozine50", "MMH",
    "IRFNA", "HTP", "Ethanol75", "Ethanol90",
    "LqdMethane", "CooledLCH4", "CooledLqdMethane",
    "LqdNH3", "MON1", "MON3",
    "PBAN",
]

# ── Telemetry polling thread ──────────────────────────────────────────────────
def telemetry_loop(krpc_host: str):
    """Continuously connects to kRPC and streams telemetry until stopped."""
    global telemetry

    while True:
        conn = None
        try:
            print(f"[kRPC] Connecting to {krpc_host} …")
            conn = krpc.connect(
                name="Dashboard",
                address=krpc_host,
                rpc_port=50000,
                stream_port=50001,
            )
            print("[kRPC] Connected.")

            vessel = conn.space_center.active_vessel
            body   = vessel.orbit.body

            # ── Build streams ─────────────────────────────────────────────
            s_altitude     = conn.add_stream(getattr, vessel.flight(), "mean_altitude")
            s_speed        = conn.add_stream(getattr, vessel.flight(body.reference_frame), "speed")
            s_apoapsis     = conn.add_stream(getattr, vessel.orbit, "apoapsis_altitude")
            s_periapsis    = conn.add_stream(getattr, vessel.orbit, "periapsis_altitude")
            s_dyn_pres     = conn.add_stream(getattr, vessel.flight(), "dynamic_pressure")
            s_thrust       = conn.add_stream(getattr, vessel, "thrust")
            s_mass         = conn.add_stream(getattr, vessel, "mass")
            s_avail_thrust = conn.add_stream(getattr, vessel, "available_thrust")
            s_stage        = conn.add_stream(getattr, vessel.control, "current_stage")
            s_mission_time  = conn.add_stream(getattr, vessel, "met")
            s_orbital_speed = conn.add_stream(getattr, vessel.orbit, "speed")

            g_surface     = body.surface_gravity
            body_has_atmo = body.has_atmosphere

            last_apoapsis  = 0
            last_periapsis = 0

            # Fuel gauge cache
            rf_names_cache = None   # RF prop names present on current stage (rebuilt on staging)

            # Engine position cache — positions only change on staging, not every frame
            engine_cache          = []    # [{x, z, eng}]
            engine_cache_stage    = None
            engine_decouple_stages = set()  # unique decouple stages of active engines

            def rebuild_engine_cache(current_stage):
                nonlocal engine_cache, engine_cache_stage, engine_decouple_stages, rf_names_cache
                engine_cache = []
                new_decouple_stages = set()
                try:
                    for eng in vessel.parts.engines:
                        try:
                            if eng.part.vessel != vessel:
                                continue
                            if not eng.active:
                                continue
                            pos = eng.part.position(vessel.reference_frame)
                            try:
                                ds = eng.part.decouple_stage
                                if isinstance(ds, int):  # include -1 (final stage, no decoupler)
                                    new_decouple_stages.add(ds)
                            except Exception:
                                pass
                            engine_cache.append({
                                "x": round(pos[0], 2),
                                "z": round(pos[2], 2),
                                "eng": eng,
                            })
                        except Exception:
                            pass
                except Exception:
                    pass
                engine_cache_stage     = current_stage
                engine_decouple_stages = new_decouple_stages
                rf_names_cache         = None  # stage changed — rebuild prop name cache

            def build_flight_streams():
                """Create (or recreate) body-relative flight streams on SOI change."""
                nonlocal s_altitude, s_speed, s_dyn_pres, body_has_atmo
                print(f"[kRPC] Rebuilding flight streams for body: {body.name}")
                for s in [s_altitude, s_speed, s_dyn_pres]:
                    try: s.remove()
                    except Exception: pass
                try:
                    ref           = body.reference_frame
                    s_altitude    = conn.add_stream(getattr, vessel.flight(ref), "mean_altitude")
                    s_speed       = conn.add_stream(getattr, vessel.flight(ref), "speed")
                    s_dyn_pres    = conn.add_stream(getattr, vessel.flight(ref), "dynamic_pressure")
                    body_has_atmo = body.has_atmosphere
                    print(f"[kRPC] Flight streams rebuilt OK for {body.name}")
                except Exception as e:
                    print(f"[kRPC] ERROR rebuilding flight streams: {e}")

            frame_count = 0

            # Physics rate tracking
            prev_real_time    = time.time()
            prev_mission_time = s_mission_time()
            physics_rate      = 1.0

            while True:
                frame_count += 1

                # Physics rate: compare this frame's game time delta to real time delta
                now_real   = time.time()
                now_game   = s_mission_time()
                real_delta = now_real - prev_real_time
                game_delta = now_game - prev_mission_time
                # Always reset so we compare frame-to-frame only
                prev_real_time    = now_real
                prev_mission_time = now_game
                # Only update rate when mission is running (MET ticking) and real delta is valid
                if real_delta > 0 and game_delta > 0.001:
                    raw_rate     = min(game_delta / real_delta, 4.0)
                    physics_rate = 0.75 * physics_rate + 0.25 * raw_rate

                # Revert detection — MET dropped, reset cached values
                if game_delta < -5:
                    last_apoapsis  = 0
                    last_periapsis = 0
                    rf_names_cache = None

                # ── SOI change detection (every 3 frames = ~0.3 s) ───────
                if frame_count % 3 == 0:
                    nonlocal_body = vessel.orbit.body
                    if nonlocal_body != body:
                        print(f"[kRPC] SOI change: {body.name} → {nonlocal_body.name}")
                        body      = nonlocal_body
                        g_surface = body.surface_gravity
                        build_flight_streams()

                stage  = s_stage()

                # Fuel: current stage only (stage index is current_stage - 1)
                resources = vessel.resources_in_decouple_stage(stage - 1, cumulative=False)

                # Stock fuels
                lf      = resources.amount("LiquidFuel") if resources.has_resource("LiquidFuel") else 0
                lf_max  = resources.max("LiquidFuel")    if resources.has_resource("LiquidFuel") else 0
                sf      = resources.amount("SolidFuel")  if resources.has_resource("SolidFuel")  else 0
                sf_max  = resources.max("SolidFuel")     if resources.has_resource("SolidFuel")  else 0
                stock_fuel     = lf + sf
                stock_fuel_max = lf_max + sf_max
                stock_fuel_pct = (stock_fuel / stock_fuel_max * 100) if stock_fuel_max > 0 else 0


                # RealFuels fuel gauge — query only the active engines' stage
                # Uses max decouple_stage so SRBs/first-to-sep engines show first.
                # When max is -1, all active engines are on the final stage (no decoupler
                # above them), so query vessel.resources directly — after separation the
                # vessel IS the final stage, so this returns only those tanks.
                try:
                    target_stage = max(engine_decouple_stages) if engine_decouple_stages else stage - 1
                    combined = _RF_PROPS_BASE + [p for p in _user_rf_props if p not in _RF_PROPS_BASE]
                    if target_stage < 0:
                        # Final-stage vehicle (e.g. Starship ship after booster sep)
                        all_res = vessel.resources
                        if rf_names_cache is None:
                            rf_names_cache = [n for n in combined if n in all_res.names]
                        rf_amt = sum(all_res.amount(n) for n in rf_names_cache)
                        rf_max = sum(all_res.max(n)    for n in rf_names_cache)
                    else:
                        # Normal stage with a decoupler — query that stage only
                        stage_res = vessel.resources_in_decouple_stage(target_stage, cumulative=False)
                        if rf_names_cache is None:
                            rf_names_cache = [n for n in combined if stage_res.has_resource(n)]
                        rf_amt = sum(stage_res.amount(n) for n in rf_names_cache)
                        rf_max = sum(stage_res.max(n)    for n in rf_names_cache)
                    rp1_fuel_pct = (rf_amt / rf_max * 100) if rf_max > 0 else 0
                except Exception:
                    rp1_fuel_pct = 0

                # Orbit values — guard against stream glitches during staging
                try:
                    last_apoapsis  = s_apoapsis()
                    last_periapsis = s_periapsis()
                except Exception:
                    pass  # keep last known good values

                mass    = s_mass()
                thrust  = s_avail_thrust()
                twr     = (thrust / (mass * g_surface)) if mass > 0 else 0

                # Engine layout: rebuild only when stage changes
                if stage != engine_cache_stage:
                    rebuild_engine_cache(stage)

                # Engine firing state: only thrust per frame (positions already cached)
                engines_data = []
                for ec in engine_cache:
                    try:
                        engines_data.append({
                            "x": ec["x"],
                            "z": ec["z"],
                            "firing": ec["eng"].thrust > 0,
                        })
                    except Exception:
                        pass


                # Speed — surface speed in atmosphere, orbital speed in vacuum
                alt_m = s_altitude()
                if body_has_atmo:
                    try:
                        static_p     = vessel.flight().static_pressure
                        display_speed = s_speed() if static_p > 100 else s_orbital_speed()
                    except Exception:
                        display_speed = s_speed()
                else:
                    display_speed = s_speed() if alt_m <= 10000 else s_orbital_speed()

                with telemetry_lock:
                    telemetry.update({
                        "connected":        True,
                        "vessel_name":      vessel.name,
                        "altitude":         round(alt_m, 1),
                        "speed":            round(display_speed, 1),
                        "apoapsis":         round(last_apoapsis, 0),
                        "periapsis":        round(last_periapsis, 0),
                        "throttle":         round((s_thrust() / s_avail_thrust() * 100) if s_avail_thrust() > 0 else 0, 1),
                        "twr":              round(twr, 2),
                        "dynamic_pressure": round(s_dyn_pres(), 0),
                        "fuel_pct":         round(stock_fuel_pct, 1),
                        "fuel_pct_rp1":     round(rp1_fuel_pct, 1),
                        "fuel_amount":      round(stock_fuel, 1),
                        "fuel_max":         round(stock_fuel_max, 1),
                        "current_stage":    stage,
                        "mission_time":     round(s_mission_time(), 1),
                        "engines":          engines_data,
                        "warp_rate":        round(physics_rate, 2),
                    })

                time.sleep(0.1)

        except krpc.error.RPCError as e:
            print(f"[kRPC] RPC error: {e}")
        except ConnectionRefusedError:
            print("[kRPC] Connection refused. Is KSP running with kRPC server open?")
        except Exception as e:
            print(f"[kRPC] Error: {e}")
        finally:
            if conn:
                try:
                    # Explicitly remove all streams before closing
                    for stream in list(conn.stream_manager.streams.values()):
                        try: stream.remove()
                        except Exception: pass
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass

        with telemetry_lock:
            telemetry["connected"] = False

        print("[kRPC] Retrying in 3 s …")
        time.sleep(3)


# ── SocketIO push thread ───────────────────────────────────────────────────────
def emit_loop():
    """Push telemetry to all connected browser clients at ~10 Hz."""
    while True:
        with telemetry_lock:
            data = dict(telemetry)
        socketio.emit("telemetry", data)
        time.sleep(0.1)


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/spacex")
def spacex():
    return render_template("spacex.html")

@app.route("/images/<path:filename>")
@app.route("/camera/<path:cam_path>")
def jrti_proxy(filename=None, cam_path=None):
    """Catch JRTI resource requests that iframe content makes against our server."""
    if filename:
        url = f"http://localhost:8080/images/{filename}"
    else:
        url = f"http://localhost:8080/camera/{cam_path}"
    try:
        req = requests.get(url, stream=True, timeout=10)
        content_type = req.headers.get("Content-Type", "application/octet-stream")
        def generate():
            try:
                for chunk in req.iter_content(chunk_size=1024):
                    if chunk:
                        yield chunk
            finally:
                req.close()
        return Response(stream_with_context(generate()), content_type=content_type)
    except Exception as e:
        return f"JRTI proxy error: {e}", 500



@app.route("/api/cameras")
def camera_list():
    """Fetch available JRTI cameras from the JRTI JSON API."""
    try:
        resp = requests.get("http://localhost:8080/cameras", timeout=5)
        data = resp.json()
        cameras = [{"id": str(cam["id"]), "name": cam.get("name", "")} for cam in data]
        return {"cameras": cameras}
    except Exception as e:
        return {"cameras": [], "error": str(e)}


# ── OBS WebSocket integration ─────────────────────────────────────────────────
try:
    import obsws_python as _obs_lib
    _OBS_LIB_OK = True
except ImportError:
    _OBS_LIB_OK = False
    print("[OBS] obsws-python not installed — run: pip install obsws-python")

_obs_client      = None
_obs_lock        = threading.Lock()
_obs_password    = ""       # set by --obs-password arg
_rec_events      = []
_rec_events_lock = threading.Lock()
_user_rf_props   = []       # extra fuel resource names set by user in dashboard settings


def _get_obs():
    global _obs_client
    if not _OBS_LIB_OK:
        return None
    with _obs_lock:
        if _obs_client is not None:
            return _obs_client
        try:
            _obs_client = _obs_lib.ReqClient(
                host="localhost", port=4455,
                password=_obs_password, timeout=3
            )
            print("[OBS] Connected.")
        except Exception as e:
            print(f"[OBS] Connect failed: {e}")
            _obs_client = None
        return _obs_client


def _drop_obs():
    global _obs_client
    with _obs_lock:
        if _obs_client:
            try: _obs_client.disconnect()
            except Exception: pass
        _obs_client = None


@app.route("/api/obs/status")
def obs_status():
    client = _get_obs()
    if not client:
        return {"connected": False, "recording": False, "duration_s": 0}
    try:
        r = client.get_record_status()
        return {
            "connected": True,
            "recording": bool(r.output_active),
            "duration_s": round((r.output_duration or 0) / 1000, 1),
        }
    except Exception:
        _drop_obs()
        return {"connected": False, "recording": False, "duration_s": 0}


@app.route("/api/obs/toggle", methods=["POST"])
def obs_toggle():
    client = _get_obs()
    if not client:
        return {"ok": False, "error": "OBS not connected"}
    try:
        client.toggle_record()
        return {"ok": True}
    except Exception as e:
        _drop_obs()
        return {"ok": False, "error": str(e)}


@app.route("/api/obs/event", methods=["POST"])
def obs_log_event():
    data = request.get_json(silent=True) or {}
    with _rec_events_lock:
        _rec_events.append({
            "name":  data.get("name",  ""),
            "met":   data.get("met",   0),
            "rec_s": data.get("rec_s", None),
        })
    return {"ok": True}


@app.route("/api/obs/events")
def obs_events():
    with _rec_events_lock:
        return {"events": list(_rec_events)}


@app.route("/api/obs/clear", methods=["POST"])
def obs_clear_events():
    with _rec_events_lock:
        _rec_events.clear()
    return {"ok": True}


@app.route("/api/fuel-props", methods=["GET", "POST"])
def fuel_props():
    global _user_rf_props
    if request.method == "POST":
        names = request.json.get("names", [])
        _user_rf_props = [n.strip() for n in names if n.strip()]
        return {"ok": True}
    return {"names": _user_rf_props}


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    global _obs_password
    parser = argparse.ArgumentParser(description="kRPC Mission Control Dashboard")
    parser.add_argument("--krpc-host", default="127.0.0.1",
                        help="kRPC server host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5000,
                        help="Dashboard web server port (default: 5000)")
    parser.add_argument("--obs-password", default="",
                        help="OBS WebSocket password (default: empty)")
    args = parser.parse_args()
    _obs_password = args.obs_password

    # Start kRPC polling thread
    t_telemetry = threading.Thread(target=telemetry_loop, args=(args.krpc_host,), daemon=True)
    t_telemetry.start()

    # Start SocketIO push thread
    t_emit = threading.Thread(target=emit_loop, daemon=True)
    t_emit.start()

    print(f"\n Mission Control Dashboard → http://localhost:{args.port}\n")
    socketio.run(app, host="0.0.0.0", port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
