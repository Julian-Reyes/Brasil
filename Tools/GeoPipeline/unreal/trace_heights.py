# Runs INSIDE the Unreal Editor (via unreal/ue_remote.py). Vertical line traces at UTM points; prints the hit
# height and actor so in-engine geometry can be compared with the pipeline's numbers.
# UNTESTED (written on the MacBook without Unreal); see Docs/Map/UnrealDebugGuide.md.
#
#   .venv/bin/python unreal/ue_remote.py unreal/trace_heights.py \
#       '{"points": [[685376, 7457447], [684695, 7457951]], "top_m": 200}'
#
# Traces complex collision from top_m down to -20 m. Water meshes have no collision, so over water the hit is the
# lake / sea bed. `ARGS` and `unreal` are injected by ue_remote.py.
import json

PROJECT = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
with open(PROJECT + "Tools/GeoPipeline/config/project.json") as f:
    origin = json.load(f)["unreal"]
world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
top = ARGS.get("top_m", 200.0) * 100.0
for e, n in ARGS["points"]:
    x = (e - origin["origin_easting"]) * 100.0
    y = -(n - origin["origin_northing"]) * 100.0
    hit = unreal.SystemLibrary.line_trace_single(
        world, unreal.Vector(x, y, top), unreal.Vector(x, y, -2000.0), unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,
        True, [], unreal.DrawDebugTrace.NONE, True)
    if hit is None:
        print(f"E{e} N{n}: no hit")
        continue
    t = hit.to_tuple()  # HitResult fields; index 4 = location, 9 = actor in UE 5.x (check if this errors)
    loc, actor = t[4], t[9]
    label = actor.get_actor_label() if actor else "?"
    print(f"E{e} N{n}: z = {loc.z / 100.0:.2f} m  ({label})")
