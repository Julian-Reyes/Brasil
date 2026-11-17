# Unreal-side import for a pipeline area, run through unreal-mcp's sandboxed ProgrammaticToolset
# (execute_tool_script). The sandbox only allows json/math/re/time/copy/datetime and execute_tool(),
# so all file access goes through AssetTools.read_file on Saved/RioPipeline/<aoi>_manifest.json.
#
# Prepend a PARAMS line before sending, e.g.:
#   PARAMS = {"aoi": "test_leblon_vidigal", "phase": "import", "start": 0, "count": 8}
#   PARAMS = {"aoi": "test_leblon_vidigal", "phase": "place"}
#
# Regeneration order: clear (in every level that uses the area) -> import -> place.
#
# Phases
#   clear   delete only the actors tagged with the area tag from the CURRENT level.
#   import  (re)import meshes [start, start+count) into the manifest's asset_root, assign the shared blockout
#           materials, replace the auto-generated convex hull with per-triangle (complex-as-simple) collision.
#           Generated assets are never hand-edited, so overwriting them is safe.
#   place   in the CURRENT level: delete only actors tagged with the area tag, then spawn one actor per
#           manifest entry with label, tags and outliner folder. Untagged (hand-made) actors are untouched.
import json

SM = "editor_toolset.toolsets.static_mesh.StaticMeshTools."
OT = "editor_toolset.toolsets.object.ObjectTools."
AT = "editor_toolset.toolsets.asset.AssetTools."
SC = "editor_toolset.toolsets.scene.SceneTools."
AC = "editor_toolset.toolsets.actor.ActorTools."
MATERIALS = "/Game/Rio/Materials/Blockout/"
PROJECT = "/Users/julianreyes/Documents/Unreal Projects/Brasil/"


def call(tool, **kw):
    return execute_tool(tool, json.dumps(kw))["returnValue"]


def manifest(aoi):
    return json.loads(call(AT + "read_file", file_path=PROJECT + "Saved/RioPipeline/" + aoi + "_manifest.json"))


def asset_path(m, entry):
    return m["asset_root"] + "/" + entry["kind"] + "/" + entry["asset"]


def import_one(m, entry):
    path = asset_path(m, entry)
    folder = path.rsplit("/", 1)[0]
    call(AT + "create_folder", path=folder)
    if call(AT + "exists", path=path):
        call(AT + "delete", path=path)
    call(SM + "import_file", folder_path=folder, asset_name=entry["asset"], source_file=entry["obj"],
         import_materials=False)
    mesh = {"refPath": path + "." + entry["asset"]}
    for slot in call(SM + "get_material_slots", mesh=mesh):
        call(SM + "set_material", mesh=mesh, slot_name=slot,
             material={"refPath": MATERIALS + slot + "." + slot})
    call(SM + "remove_collisions", mesh=mesh)
    body = json.loads(call(OT + "get_properties", instance=mesh, properties=["BodySetup"]))["BodySetup"]
    flag = "CTF_UseComplexAsSimple" if entry["collision"] == "complex_as_simple" else "CTF_UseSimpleAsComplex"
    call(OT + "set_properties", instance=body, values=json.dumps({"CollisionTraceFlag": flag}))
    return {"asset": path, "tris": call(SM + "get_triangle_count", mesh=mesh),
            "bounds": call(SM + "get_bounds", mesh=mesh)}


def clear(m):
    removed = 0
    for actor in call(SC + "find_actors", name="", tag=m["tags"][1], collision_channels=[]):
        removed += 1 if call(SC + "remove_from_scene", actor=actor) else 0
    return removed


def place(m):
    removed = clear(m)
    placed = []
    for entry in m["actors"]:
        x, y, z = entry["location_cm"]
        path = asset_path(m, entry)
        actor = call(SC + "add_to_scene_from_asset", asset_path=path, name=entry["label"],
                     xform={"location": {"x": x, "y": y, "z": z}})
        call(AC + "set_label", actor=actor, label=entry["label"])
        for tag in m["tags"] + ["RioKind:" + entry["kind"]]:
            call(AC + "add_tag", actor=actor, tag=tag)
        call(SC + "set_actor_folder", actor=actor, folder_path=m["outliner_root"] + "/" + entry["kind"])
        placed.append(entry["label"])
    return {"removed": removed, "placed": len(placed)}


def run():
    m = manifest(PARAMS["aoi"])
    if PARAMS["phase"] == "import":
        entries = m["actors"][PARAMS.get("start", 0):PARAMS.get("start", 0) + PARAMS.get("count", 1000)]
        done = [import_one(m, e) for e in entries]
        saved = call(AT + "save_assets", asset_paths=[d["asset"] for d in done])
        return {"imported": len(done), "saved": saved, "tris": sum(d["tris"] for d in done),
                "first": done[0] if done else None}
    if PARAMS["phase"] == "clear":
        return {"removed": clear(m)}
    if PARAMS["phase"] == "place":
        return place(m)
    raise ValueError("unknown phase")
