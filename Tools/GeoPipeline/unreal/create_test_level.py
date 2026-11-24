# Runs INSIDE the Unreal Editor (via unreal/ue_remote.py). Creates a 1:1 source test level like
# L_Rio_Test_LeblonArpoador: World Partition, GeoReferencingSystem (FlatPlanet, EPSG:31983 / EPSG:4674, origin
# E684500 N7461000), the Basic-template sky / light / fog set and an optional PlayerStart. Does nothing if the
# level already exists. Leaves the new level open.
#
#   .venv/bin/python unreal/ue_remote.py unreal/create_test_level.py \
#       '{"level": "/Game/Rio/Maps/L_Rio_Test_Lagoa", "player_start": {"name": "PlayerStart_Lagoa",
#         "location_cm": [x, y, z], "yaw": 0}}'
# `ARGS` and `unreal` are injected by ue_remote.py.

level = ARGS["level"]
les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

if unreal.EditorAssetLibrary.does_asset_exist(level):
    print("exists, not touched:", level)
else:
    if not les.new_level(level, True):  # is_partitioned_world
        raise RuntimeError("new_level failed: " + level)
    O = unreal.Vector(0, 0, 0)

    def spawn(cls, label, loc=O, rot=unreal.Rotator(0, 0, 0)):
        a = actor_sub.spawn_actor_from_class(cls, loc, rot)
        a.set_actor_label(label)
        a.set_folder_path("Lighting" if cls is not unreal.GeoReferencingSystem else "")
        a.set_editor_property("is_spatially_loaded", False)
        return a

    g = spawn(unreal.GeoReferencingSystem, "GeoReferencingSystem")
    g.set_editor_property("planet_shape", unreal.PlanetShape.FLAT_PLANET)
    g.set_editor_property("projected_crs", "EPSG:31983")
    g.set_editor_property("geographic_crs", "EPSG:4674")
    g.set_editor_property("origin_at_planet_center", False)
    g.set_editor_property("origin_location_in_projected_crs", True)
    g.set_editor_property("origin_projected_coordinates_easting", 684500.0)
    g.set_editor_property("origin_projected_coordinates_northing", 7461000.0)
    g.set_editor_property("origin_projected_coordinates_up", 0.0)

    dl = spawn(unreal.DirectionalLight, "DirectionalLight", rot=unreal.Rotator(roll=112.360672, pitch=-16.285559, yaw=43.731944))
    c = dl.get_component_by_class(unreal.DirectionalLightComponent)
    c.set_editor_property("mobility", unreal.ComponentMobility.MOVABLE)
    c.set_editor_property("intensity", 6.0)
    c.set_editor_property("atmosphere_sun_light", True)
    c.set_editor_property("use_temperature", True)
    c.set_editor_property("temperature", 6500.0)

    spawn(unreal.SkyAtmosphere, "SkyAtmosphere")
    sl = spawn(unreal.SkyLight, "SkyLight")
    c = sl.get_component_by_class(unreal.SkyLightComponent)
    c.set_editor_property("mobility", unreal.ComponentMobility.MOVABLE)
    c.set_editor_property("real_time_capture", True)

    fog = spawn(unreal.ExponentialHeightFog, "ExponentialHeightFog", loc=unreal.Vector(-5600, -50, -6850))
    c = fog.get_component_by_class(unreal.ExponentialHeightFogComponent)
    c.set_editor_property("fog_density", 0.0436)
    c.set_editor_property("fog_height_falloff", 0.2)
    spawn(unreal.VolumetricCloud, "VolumetricCloud")

    sky = actor_sub.spawn_actor_from_object(unreal.load_asset("/Engine/EngineSky/SM_SkySphere"), O)
    sky.set_actor_label("SM_SkySphere")
    sky.set_folder_path("Lighting")
    sky.set_actor_scale3d(unreal.Vector(400, 400, 400))
    sky.set_editor_property("is_spatially_loaded", False)
    smc = sky.get_component_by_class(unreal.StaticMeshComponent)
    smc.set_material(0, unreal.load_asset("/Engine/EngineSky/M_SimpleSkyDome"))
    smc.set_editor_property("cast_shadow", False)

    ps = ARGS.get("player_start")
    if ps:
        x, y, z = ps["location_cm"]
        p = actor_sub.spawn_actor_from_class(unreal.PlayerStart, unreal.Vector(x, y, z),
                                             unreal.Rotator(0, 0, ps.get("yaw", 0)))
        p.set_actor_label(ps["name"])
        p.set_folder_path("Gameplay")
        p.set_editor_property("is_spatially_loaded", False)

    unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
    print("created", level)

# Always (re)open: right after new_level the editor world reports no actors until the level is loaded again.
les.load_level(level)
print("open:", unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world().get_path_name())
