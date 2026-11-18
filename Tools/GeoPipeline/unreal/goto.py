# Runs INSIDE the Unreal Editor (via unreal/ue_remote.py). Moves the level viewport camera to look at a point.
# UNTESTED (written on the MacBook without Unreal); see Docs/Map/UnrealDebugGuide.md.
#
#   .venv/bin/python unreal/ue_remote.py unreal/goto.py '{"ue": [87600, 355300, 5090]}'
#   .venv/bin/python unreal/ue_remote.py unreal/goto.py '{"utm": [685376, 7457447, 50.9], "dist_m": 60}'
#
# "ue" = Unreal source-frame centimetres; "utm" = EPSG:31983 metres (converted with config unreal.origin_*).
# The camera is placed dist_m (default 80 m) back and up from the target at a 35 degree pitch, looking north.
# `ARGS` and `unreal` are injected by ue_remote.py.
import json
import math

PROJECT = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
with open(PROJECT + "Tools/GeoPipeline/config/project.json") as f:
    origin = json.load(f)["unreal"]

if "ue" in ARGS:
    tx, ty, tz = ARGS["ue"]
else:
    e, n, z = ARGS["utm"]
    tx = (e - origin["origin_easting"]) * 100.0
    ty = -(n - origin["origin_northing"]) * 100.0
    tz = (z - origin["origin_height"]) * 100.0
dist = ARGS.get("dist_m", 80.0) * 100.0
pitch = ARGS.get("pitch_deg", -35.0)
yaw = ARGS.get("yaw_deg", -90.0)  # UE yaw -90 = looking towards -Y = north in our frame
back = dist * math.cos(math.radians(-pitch))
cam = unreal.Vector(tx - back * math.cos(math.radians(yaw)), ty - back * math.sin(math.radians(yaw)),
                    tz + dist * math.sin(math.radians(-pitch)))
unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(
    cam, unreal.Rotator(roll=0.0, pitch=pitch, yaw=yaw))
print(f"camera at {cam} looking at ({tx:.0f}, {ty:.0f}, {tz:.0f})")
