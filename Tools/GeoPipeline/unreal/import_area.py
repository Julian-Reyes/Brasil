# Runs INSIDE the Unreal Editor (via unreal/ue_remote.py). Imports and places one pipeline area into the
# currently open level from Saved/RioPipeline/<aoi>_manifest.json.
#
#   .venv/bin/python unreal/ue_remote.py unreal/import_area.py '{"aoi": "test_leblon_vidigal"}'
#
# Steps
#   1. Load (World Partition) and delete every actor tagged RioArea:<aoi>. Only generated actors carry the tag;
#      hand-made work is never touched. Loading first matters: unloaded actors are invisible to actor queries
#      and would otherwise survive as duplicates.
#   2. Re-import each mesh in place (replace_existing) under the manifest's asset_root, bind slots to the shared
#      /Game/Rio/Materials/Blockout materials, swap the importer's convex hulls for per-triangle collision.
#   3. Spawn one actor per manifest entry with label, tags, outliner folder and spatial-loading flag.
#   4. Save.
# `ARGS` and `unreal` are injected by ue_remote.py.
import json

MATERIALS = "/Game/Rio/Materials/Blockout/"
PROJECT = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())

aoi = ARGS["aoi"]
with open(PROJECT + "Saved/RioPipeline/" + aoi + "_manifest.json") as f:
    m = json.load(f)
area_tag = m["tags"][1]
actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
mesh_sub = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
labels = {e["label"] for e in m["actors"]}

# 1. clear previously generated actors (load candidates first)
WP = unreal.WorldPartitionBlueprintLibrary
candidates = [d.guid for d in WP.get_actor_descs() if str(d.label) in labels]  # empty for non-WP levels
if candidates:
    WP.load_actors(candidates)
old = [a for a in actor_sub.get_all_level_actors() if a.actor_has_tag(area_tag)]
for a in old:
    actor_sub.destroy_actor(a)
print(f"cleared {len(old)} generated actors")

# 2. import meshes
tasks = []
for e in m["actors"]:
    t = unreal.AssetImportTask()
    t.filename = e["obj"]
    t.destination_path = m["asset_root"] + "/" + e["kind"]
    t.destination_name = e["asset"]
    t.automated = True
    t.replace_existing = True
    t.save = False
    tasks.append(t)
asset_tools.import_asset_tasks(tasks)

meshes = {}
for e in m["actors"]:
    path = m["asset_root"] + "/" + e["kind"] + "/" + e["asset"]
    mesh = unreal.load_asset(path)
    if mesh is None:
        raise RuntimeError("import failed: " + path)
    # set_material per index; editing a copy of static_materials does not stick
    for i, slot in enumerate(mesh.static_materials):
        mat = unreal.load_asset(MATERIALS + str(slot.material_slot_name))
        if mat is None:  # e.g. beach slots before SM_BeachPalette.obj was imported once (see GeoPipeline README)
            raise RuntimeError("missing material " + MATERIALS + str(slot.material_slot_name) + " for " + path)
        mesh.set_material(i, mat)
    # Nanite off: complex collision would come from the simplified Nanite fallback mesh, not the real surface
    nanite = mesh.get_editor_property("nanite_settings")
    nanite.enabled = False
    mesh.set_editor_property("nanite_settings", nanite)
    mesh_sub.remove_collisions(mesh)
    body = mesh.get_editor_property("body_setup")
    body.set_editor_property("collision_trace_flag",
                             unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE if e["collision"] == "complex_as_simple"
                             else unreal.CollisionTraceFlag.CTF_USE_SIMPLE_AS_COMPLEX)
    unreal.EditorAssetLibrary.save_loaded_asset(mesh)
    meshes[e["asset"]] = mesh
print(f"imported {len(meshes)} meshes")

# 3. place
for e in m["actors"]:
    x, y, z = e["location_cm"]
    a = actor_sub.spawn_actor_from_object(meshes[e["asset"]], unreal.Vector(x, y, z))
    a.set_actor_label(e["label"])
    a.tags = [unreal.Name(t) for t in m["tags"] + ["RioKind:" + e["kind"]]]
    a.set_folder_path(m["outliner_root"] + "/" + e["kind"])
    a.set_editor_property("is_spatially_loaded", not m["always_loaded"])
print(f"placed {len(m['actors'])} actors (always_loaded={m['always_loaded']})")

# 4. save level + external actor packages
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
print("saved")
